import streamlit as st
from datetime import datetime, time as time_type, timedelta
import json
import os
import statistics
import time
import firebase_admin
from firebase_admin import credentials, auth, firestore, _apps
import base64
import urllib.parse
import pandas as pd
import plotly.graph_objects as go
import openai
from dotenv import load_dotenv
from google.cloud.firestore_v1 import Increment
import pytz

# --- Streak Calculation ---
def calculate_streaks(logs):
    """
    Calculate the current and longest streak of consecutive days with sleep logs.
    logs: list of dicts with 'date' in 'YYYY-MM-DD'.
    Returns: (current_streak, longest_streak)
    """
    from datetime import datetime, timedelta
    if not logs:
        return 0, 0
    # Sort logs by date descending
    sorted_logs = sorted(logs, key=lambda x: x.get("date", ""), reverse=True)
    streak = 0
    longest = 0
    prev_date = None
    for log in sorted_logs:
        log_date_str = log.get("date")
        if not log_date_str:
            continue
        try:
            log_date = datetime.strptime(log_date_str, "%Y-%m-%d")
        except Exception:
            continue
        if prev_date is None:
            streak = 1
        else:
            if prev_date - log_date == timedelta(days=1):
                streak += 1
            elif prev_date - log_date > timedelta(days=1):
                break  # streak ended
        prev_date = log_date
    # Now, calculate the longest streak
    longest = 0
    temp_streak = 1
    for i in range(1, len(sorted_logs)):
        try:
            d1 = datetime.strptime(sorted_logs[i-1].get("date", ""), "%Y-%m-%d")
            d2 = datetime.strptime(sorted_logs[i].get("date", ""), "%Y-%m-%d")
        except Exception:
            continue
        if (d1 - d2) == timedelta(days=1):
            temp_streak += 1
        else:
            if temp_streak > longest:
                longest = temp_streak
            temp_streak = 1
    if temp_streak > longest:
        longest = temp_streak
    return streak, longest

# --- Get the absolute path of the script's directory ---
_this_file = os.path.abspath(__file__)
_this_dir = os.path.dirname(_this_file)
AVATAR_DIR = os.path.join(_this_dir, "data", "avatars")
os.makedirs(AVATAR_DIR, exist_ok=True)

# --- Load OpenAI API Key from .env2 ---
load_dotenv(os.path.join(_this_dir, ".env2"))
openai.api_key = os.getenv("OPENAI_API_KEY")

# --- User Usage Tracking Functions ---
def get_user_usage(uid):
    """Fetch the user's usage stats from Firestore."""
    if db:
        try:
            doc = db.collection('user_usage').document(uid).get()
            if doc.exists:
                return doc.to_dict()
        except Exception as e:
            st.error(f"Error fetching usage: {e}")
    return {"messages": 0}

def increment_user_usage(uid):
    """Increment the user's message count in Firestore."""
    if db:
        try:
            usage_ref = db.collection('user_usage').document(uid)
            usage_ref.set({"messages": Increment(1)}, merge=True)
        except Exception as e:
            st.error(f"Error updating usage: {e}")

# --- Helper function for custom day labels ---
def get_day_label(day):
    """Returns 'Th' for Thursday, otherwise the first initial of the day."""
    weekday = day.strftime('%a')
    if weekday == 'Thu':
        return 'Th'
    return weekday[0]


# --- Get the absolute path of the script's directory ---
_this_file = os.path.abspath(__file__)
_this_dir = os.path.dirname(_this_file)
ASSETS_DIR = os.path.join(_this_dir, "assets")

# --- Firebase Admin SDK Setup ---
# Load credentials from the service account key JSON file
# Using a raw string (r"...") to handle Windows paths correctly
try:
    cred = credentials.Certificate(r"C:\Users\sween\Downloads\sleepaid\sleepaid-c10bf-firebase-adminsdk-fbsvc-9fc57fd56d.json")
    # Initialize Firebase if not already initialized
    if not _apps:
        firebase_admin.initialize_app(cred)
    db = firestore.client()
except Exception as e:
    st.error(f"Failed to initialize Firebase: {e}")
    st.info("Please ensure your Firebase service account key is correctly placed and the path is correct.")
    db = None

# --- Session State Initialization ---
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
if 'user_uid' not in st.session_state:
    st.session_state.user_uid = None
if 'page' not in st.session_state:
    st.session_state.page = "login" # Default to login

# --- Authentication Functions ---
def signup(email, password):
    try:
        user = auth.create_user(email=email, password=password)
        st.session_state.logged_in = True
        st.session_state.user_uid = user.uid
        # Use page from query param if present, else default to dashboard
        page_from_url = st.query_params.get("page", ["dashboard"])[0]
        st.session_state.page = page_from_url
        st.success("✅ Account created successfully! Welcome.")
        st.rerun()
    except Exception as e:
        st.error(f"❌ Error creating account: {e}")

def login(email, password):
    try:
        # Note: Firebase Admin SDK does not verify passwords.
        # This is a simplified check. For production, use a client-side SDK.
        user = auth.get_user_by_email(email)
        st.session_state.logged_in = True
        st.session_state.user_uid = user.uid
        # Use page from query param if present, else default to dashboard
        page_from_url = st.query_params.get("page", ["dashboard"])[0]
        st.session_state.page = page_from_url
        st.success("✅ Logged in successfully!")
        st.rerun()
    except Exception as e:
        st.error(f"❌ Login failed: Invalid email or password.")

def logout():
    st.session_state.logged_in = False
    st.session_state.user_uid = None
    st.session_state.page = "login"
    # When we logout, we want to clear all query params and go to a clean login state
    if "action" in st.query_params:
        st.query_params.clear()
    st.rerun()

# --- Firestore Data Functions ---
def load_user_logs(uid):
    logs = []
    if db:
        try:
            logs_ref = db.collection('users').document(uid).collection('sleep_logs').order_by('date', direction="DESCENDING").stream()
            for log in logs_ref:
                logs.append(log.to_dict())
        except Exception as e:
            st.error(f"Error loading logs: {e}")
    return logs

def save_user_log(uid, log_data):
    if db:
        try:
            # Use date as the document ID for easy lookup
            doc_id = log_data['date']
            db.collection('users').document(uid).collection('sleep_logs').document(doc_id).set(log_data)
            return True
        except Exception as e:
            st.error(f"Error saving log: {e}")
            return False
    return False

def get_user_profile(uid):
    if db:
        try:
            doc_ref = db.collection('user_profiles').document(uid)
            doc = doc_ref.get()
            if doc.exists:
                data = doc.to_dict()
                if not data or not isinstance(data, dict):
                    return None
                # Migrate legacy profile to new structure if needed
                if 'personal_info' not in data or 'sleep_patterns' not in data or 'lifestyle_support' not in data:
                    # Try to infer from legacy fields
                    onboarding = data.get('onboarding', {})
                    # Personal info
                    personal_info = {
                        'first_name': onboarding.get('first_name', ''),
                        'age': onboarding.get('age', ''),
                        'gender': onboarding.get('gender', ''),
                        'timezone': onboarding.get('timezone', 'UTC'),
                    }
                    # Sleep patterns
                    sleep_patterns = {
                        'struggle': onboarding.get('struggle', ''),
                        'goal': onboarding.get('goal', ''),
                        'goal_custom': onboarding.get('goal_custom', ''),
                        'usual_bedtime': onboarding.get('usual_bedtime', '23:00'),
                        'usual_wake_time': onboarding.get('usual_wake_time', '07:00'),
                    }
                    # Lifestyle/support
                    lifestyle_support = {
                        'workout': onboarding.get('workout', ''),
                        'workout_freq': onboarding.get('workout_freq', 0),
                        'caffeine': onboarding.get('caffeine', ''),
                        'caffeine_time': onboarding.get('caffeine_time', ''),
                        'phone_use': onboarding.get('phone_use', ''),
                        'support_pref': onboarding.get('support_pref', ''),
                    }
                    data['personal_info'] = personal_info
                    data['sleep_patterns'] = sleep_patterns
                    data['lifestyle_support'] = lifestyle_support
                return data
        except Exception as e:
            st.error(f"Error getting profile: {e}")
    return None

def save_user_profile(uid, profile_data):
    if db:
        try:
            db.collection('user_profiles').document(uid).set(profile_data)
            return True
        except Exception as e:
            st.error(f"Error saving profile: {e}")
            return False
    return False

# --- Onboarding Form ---
def show_onboarding_form():
    # Track onboarding page in session state
    if 'onboarding_page' not in st.session_state:
        st.session_state.onboarding_page = 1
    if 'onboarding_data' not in st.session_state:
        st.session_state.onboarding_data = {}

    page = st.session_state.onboarding_page
    onboarding_data = st.session_state.onboarding_data

    if page == 1:
        st.markdown("# 👤 Personal Profile")
        st.markdown("Who are you? Help us personalize your experience.")
        with st.form("onboarding_profile_form"):
            first_name = st.text_input("First Name", value=onboarding_data.get('first_name', ''))
            age = st.text_input("Age", value=onboarding_data.get('age', ''))
            gender = st.selectbox("Gender (optional)", ["", "Male", "Female", "Other"], index=["", "Male", "Female", "Other"].index(onboarding_data.get('gender', '')))
            timezone_list = pytz.all_timezones
            timezone = st.selectbox("Time Zone", timezone_list, index=timezone_list.index(onboarding_data.get('timezone', 'UTC')) if onboarding_data.get('timezone', 'UTC') in timezone_list else 0)
            avatar_file = st.file_uploader("Profile Avatar (optional)", type=["png", "jpg", "jpeg"])
            submitted = st.form_submit_button("Next →")
        if submitted:
            errors = []
            if not (first_name or "").strip():
                errors.append("First name is required.")
            age_val = None
            try:
                if age is None or not str(age).strip():
                    raise ValueError("Age is required.")
                age_val = int(age)
                if age_val <= 0 or age_val > 120:
                    errors.append("Age must be a positive number less than 120.")
            except ValueError:
                errors.append("Please enter a valid number for age.")
            if not timezone:
                errors.append("Time zone is required.")
            if errors:
                for err in errors:
                    st.error(err)
                st.stop()
            onboarding_data['first_name'] = (first_name or '').strip()
            onboarding_data['age'] = age_val
            onboarding_data['gender'] = gender
            onboarding_data['timezone'] = timezone
            # Save avatar if uploaded
            if avatar_file:
                avatar_path = os.path.join(AVATAR_DIR, f"{st.session_state.user_uid}.png")
                with open(avatar_path, "wb") as f:
                    f.write(avatar_file.read())
            st.session_state.onboarding_data = onboarding_data
            st.session_state.onboarding_page = 2
            st.rerun()

    elif page == 2:
        st.markdown("# 💤 Sleep Patterns")
        st.markdown("Understand your current habits and challenges.")
        with st.form("onboarding_sleep_form"):
            struggle = st.selectbox(
                "What's your biggest sleep struggle?",
                ["Falling asleep", "Waking up during the night", "Waking up too early", "Staying consistent"],
                index=["Falling asleep", "Waking up during the night", "Waking up too early", "Staying consistent"].index(onboarding_data.get('struggle', "Falling asleep"))
            )
            goal = st.selectbox(
                "What's your main sleep goal?",
                ["Sleep 7+ hours", "No caffeine after 6pm", "Log my sleep daily", "Go to bed before 11pm", "Wake up at the same time", "Custom goal"],
                index=["Sleep 7+ hours", "No caffeine after 6pm", "Log my sleep daily", "Go to bed before 11pm", "Wake up at the same time", "Custom goal"].index(onboarding_data.get('goal', "Sleep 7+ hours"))
            )
            goal_custom = ""
            if goal == "Custom goal":
                goal_custom = st.text_input("Describe your custom sleep goal:", value=onboarding_data.get('goal_custom', ''))
            usual_bedtime = st.time_input("What time do you usually go to bed?", value=onboarding_data.get('usual_bedtime', time_type(23, 0)))
            usual_wake_time = st.time_input("What time do you usually wake up?", value=onboarding_data.get('usual_wake_time', time_type(7, 0)))
            submitted = st.form_submit_button("Next →")
        if submitted:
            errors = []
            if goal == "Custom goal" and not (goal_custom or "").strip():
                errors.append("Please enter your custom goal.")
            if not usual_bedtime:
                errors.append("Usual bedtime is required.")
            if not usual_wake_time:
                errors.append("Usual wake time is required.")
            if errors:
                for err in errors:
                    st.error(err)
                st.stop()
            onboarding_data['struggle'] = struggle
            onboarding_data['goal'] = goal
            onboarding_data['goal_custom'] = (goal_custom or '').strip() if goal == "Custom goal" else ''
            onboarding_data['usual_bedtime'] = usual_bedtime.strftime("%H:%M") if usual_bedtime else ''
            onboarding_data['usual_wake_time'] = usual_wake_time.strftime("%H:%M") if usual_wake_time else ''
            st.session_state.onboarding_data = onboarding_data
            st.session_state.onboarding_page = 3
            st.rerun()

    elif page == 3:
        st.markdown("# 🌱 Lifestyle & Support Preferences")
        st.markdown("What factors affect your sleep, and how can we help?")
        with st.form("onboarding_lifestyle_form"):
            workout = st.radio("Do you have a workout routine?", ["Yes", "No"], index=0 if onboarding_data.get('workout', 'No') == 'Yes' else 1)
            workout_freq = 0
            if workout == "Yes":
                workout_freq = st.number_input("How many times per week?", min_value=1, max_value=14, value=onboarding_data.get('workout_freq', 3))
            caffeine = st.radio("Do you use caffeine?", ["Yes", "No"], index=0 if onboarding_data.get('caffeine', 'No') == 'Yes' else 1)
            caffeine_time = ""
            if caffeine == "Yes":
                caffeine_time = st.time_input("Time of last caffeine?", value=onboarding_data.get('caffeine_time', time_type(15, 0)))
            phone_use = st.radio("Do you use your phone at night?", ["Yes", "No"], index=0 if onboarding_data.get('phone_use', 'Yes') == 'Yes' else 1)
            support_pref = st.text_area("What would you like SleepAid to help with most?", value=onboarding_data.get('support_pref', ''))
            submitted = st.form_submit_button("Finish & Start Journey →")
        if submitted:
            onboarding_data['workout'] = workout
            onboarding_data['workout_freq'] = workout_freq if workout == "Yes" else 0
            onboarding_data['caffeine'] = caffeine
            onboarding_data['caffeine_time'] = caffeine_time.strftime("%H:%M") if caffeine == "Yes" and caffeine_time else ''
            onboarding_data['phone_use'] = phone_use
            onboarding_data['support_pref'] = (support_pref or '').strip()
            # Save all onboarding data to Firestore profile
            profile_data = {
                "personal_info": {
                    "first_name": onboarding_data.get('first_name', ''),
                    "age": onboarding_data.get('age', ''),
                    "gender": onboarding_data.get('gender', ''),
                    "timezone": onboarding_data.get('timezone', ''),
                },
                "sleep_patterns": {
                    "struggle": onboarding_data.get('struggle', ''),
                    "goal": onboarding_data.get('goal', ''),
                    "goal_custom": onboarding_data.get('goal_custom', ''),
                    "usual_bedtime": onboarding_data.get('usual_bedtime', ''),
                    "usual_wake_time": onboarding_data.get('usual_wake_time', ''),
                },
                "lifestyle_support": {
                    "workout": onboarding_data.get('workout', ''),
                    "workout_freq": onboarding_data.get('workout_freq', 0),
                    "caffeine": onboarding_data.get('caffeine', ''),
                    "caffeine_time": onboarding_data.get('caffeine_time', ''),
                    "phone_use": onboarding_data.get('phone_use', ''),
                    "support_pref": onboarding_data.get('support_pref', ''),
                },
                "onboarding_complete": True,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat()
            }
            if save_user_profile(st.session_state.user_uid, profile_data):
                st.session_state.onboarding_data = {}
                st.session_state.onboarding_page = 1
                st.success(f"Welcome {profile_data['personal_info']['first_name']}, your sleep journey starts today.")
                st.session_state.page = "dashboard"
                st.rerun()
            else:
                st.error("❌ There was an issue saving your profile. Please try again.")



# Helper function to encode images
def get_image_as_base64(path):
    # Check if the file exists to avoid errors
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        data = f.read()
    return base64.b64encode(data).decode()

# --- Custom Font and Global Styles ---
st.markdown("""
    <style>
    /* --- Body and App Background --- */
    body, .stApp {
        background: linear-gradient(135deg, #1C191E 0%,rgb(48, 1, 118) 100%) !important;
        min-height: 100vh;
    }

    /* Make Streamlit header transparent */
    header[data-testid="stHeader"] {
        background: transparent !important;
    }

    /* --- Generic Card Container (for Dashboard, Goal Card, etc.) --- */
    .metric-container {
        background: #232026;
        border: 1px solid #28242C;
        border-radius: 18px;
        padding: 1.5rem 2rem;
    }

    /* --- Dashboard Tab Buttons (Gradient) --- */
    div[data-testid="stVerticalBlockBorderWrapper"] div[data-testid="stButton"] > button {
        background: linear-gradient(135deg, #8E05C2 0%, #C084FC 100%) !important;
        border: none !important;
        font-weight: 600;
        transition: transform 0.2s, box-shadow 0.2s !important;
    }
    div[data-testid="stVerticalBlockBorderWrapper"] div[data-testid="stButton"] > button:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 20px rgba(142, 5, 194, 0.4);
    }
    
    /* --- Make Dashboard tab container transparent --- */
    .dashboard-tabs-container div[data-testid="stVerticalBlockBorderWrapper"] {
        background: transparent !important;
        border: none !important;
    }

    /* --- Dashboard Log Sleep Button (Gradient) --- */
    .log-sleep-button-container div[data-testid="stButton"] > button {
        background: linear-gradient(135deg, #8E05C2 0%, #C084FC 100%) !important;
        border: none !important;
        color: #fff !important;
        border-radius: 10px !important;
        font-weight: 600;
        transition: transform 0.2s, box-shadow 0.2s !important;
    }
     .log-sleep-button-container div[data-testid="stButton"] > button:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 20px rgba(142, 5, 194, 0.4);
    }
    
    .log-sleep-button-container {
        margin-top: 1.5rem;
    }
    
    /* --- Sidebar Button Styles --- */
    section[data-testid="stSidebar"] {
        /* Remove problematic width/padding overrides to allow Streamlit to handle collapse/expand */
        /* min-width: unset !important; */
        /* max-width: unset !important; */
        /* width: unset !important; */
        /* padding-left: 1rem !important; */
        /* padding-right: 1rem !important; */
    }
    section[data-testid="stSidebar"] button {
        width: 100% !important; min-width: 120px !important; height: 44px !important;
        background: transparent !important; color: #CCC8CF !important; border: none !important;
        border-radius: 10px !important; margin-bottom: 0.5rem !important;
        text-align: left !important; padding-left: 1rem !important;
        transition: background 0.2s, color 0.2s, transform 0.2s;
    }
    section[data-testid="stSidebar"] button:hover {
        background: #232026 !important; color: #C084FC !important;
        box-shadow: 0 4px 16px 0 rgba(142,5,194,0.10), 0 1.5px 6px 0 rgba(0,0,0,0.10);
        transform: translateY(-2px) scale(1.03);
    }
    
    /* --- Fix: Make the expand/collapse sidebar button flush to the left edge --- */
    [data-testid="stSidebarCollapseControl"] {
        position: fixed !important;
        left: 0 !important;
        top: 2rem !important; /* adjust as needed */
        margin: 0 !important;
        padding: 0 !important;
        z-index: 1001 !important;
        border-radius: 0 8px 8px 0 !important;
        box-shadow: none !important;
    }
    
    /* Remove any margin/padding on main containers that could cause a gap */
    body, .stApp, section[data-testid="main"] {
        margin-left: 0 !important;
        padding-left: 0 !important;
    }
    
    /* --- Me Page 7-Day Trend Container --- */
    .me-page-trend-container div[data-testid="stVerticalBlockBorderWrapper"] {
        background-color: #232026;
        border: 1px solid #4A4A4A;
        border-radius: 18px;
        padding: 1.5rem;
        margin-top: 1.5rem;
    }

    /* --- Me Page Specific Profile Card Styles --- */
    .me-card {
        background: #232026; border-radius: 16px; padding: 1.5rem 1rem;
        display: flex; align-items: center; gap: 1.5rem;
        margin-bottom: 1.5rem; border: 1px solid #4A4A4A;
    }
    .me-avatar {
        background: linear-gradient(135deg, #8E05C2 0%, #6A00AC 100%);
        color: #fff; font-size: 2.2rem; font-weight: 700; border-radius: 50%;
        width: 64px; height: 64px; display: flex; align-items: center; justify-content: center;
    }
    .me-info h3 { margin: 0 0 0.5rem 0; color: #fff; font-size: 1.5rem; }
    .me-metrics { display: flex; gap: 2rem; }
    .me-metric-label { color: #C084FC; font-size: 0.95rem; display: block; }
    .me-metric-value { color: #fff; font-size: 1.2rem; font-weight: 600; }

    /* --- Layout Fix --- */
    section[data-testid="main"] {
        max-width: 100%;
        overflow-x: hidden;
    }

    /* --- Mobile Responsive Styles --- */
    @media (max-width: 600px) {
      .me-card, .metric-container, .me-page-trend-container {
        padding: 1rem 0.5rem !important;
        border-radius: 10px !important;
      }
      .me-info h3 {
        font-size: 1.1rem !important;
      }
      .me-metric-value {
        font-size: 1rem !important;
      }
      .csv-download-btn-container .stDownloadButton > button {
        width: 100% !important;
        font-size: 1rem !important;
        padding: 0.7rem 0 !important;
      }
      .stDataFrameContainer {
        font-size: 0.9rem !important;
      }
    }
    </style>
""", unsafe_allow_html=True)

# --- Helper Functions ---
def load_logs():
    logs = []
    if os.path.exists("data/sleep_logs.json"):
        with open("data/sleep_logs.json", "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    logs.append(json.loads(line))
                except json.JSONDecodeError:
                    print(f"Warning: Skipping invalid JSON line: {line}")
    return logs

def calculate_sleep_score(log, user_profile, consistency):
    """
    Personalized sleep score based on user onboarding preferences and daily log.
    Args:
        log (dict): The sleep log for the day.
        user_profile (dict): The user's onboarding profile.
        consistency (float): Minutes difference in bedtime from previous day.
    Returns:
        int: Sleep score (0-100)
    """
    score = 0
    # --- Configurable weights for scalability ---
    weights = {
        "duration": 0.25,
        "latency": 0.15,
        "wakeups": 0.10,
        "energy": 0.10,
        "consistency": 0.10,
        "efficiency": 0.15,
        "environment": 0.10,
        "stress": 0.05,
    }
    # --- 1. Personalized Sleep Duration ---
    hours = float(log.get("hours_slept", 0))
    sleep_habits = user_profile.get("sleep_habits", {})
    goal = sleep_habits.get("sleep_duration_goal", "7-8 hours")
    # Map goal to numeric range
    goal_ranges = {
        "<6 hours": (0, 6),
        "6-7 hours": (6, 7),
        "7-8 hours": (7, 8),
        "8+ hours": (8, 24),
    }
    min_goal, max_goal = goal_ranges.get(goal, (7, 8))
    # Give full points for being within goal, partial for close, less for far
    if min_goal <= hours <= max_goal:
        score += 100 * weights["duration"]
    elif (min_goal - 0.5) <= hours < min_goal or max_goal < hours <= (max_goal + 0.5):
        score += 75 * weights["duration"]
    elif (min_goal - 1) <= hours < (min_goal - 0.5) or (max_goal + 0.5) < hours <= (max_goal + 1):
        score += 50 * weights["duration"]
    else:
        score += 20 * weights["duration"]

    # --- 2. Sleep Onset Latency (personalized if user provided) ---
    onset_latency = int(log.get("time_to_fall_asleep", 15))
    user_latency_goal = sleep_habits.get("time_to_fall_asleep", 20)
    if onset_latency <= user_latency_goal:
        score += 100 * weights["latency"]
    elif onset_latency <= user_latency_goal + 10:
        score += 70 * weights["latency"]
    else:
        score += 30 * weights["latency"]

    # --- 3. Wakeups (personalized if user wakes up at night) ---
    night_patterns = user_profile.get("night_patterns", {})
    wakes_up_at_night = night_patterns.get("wakes_up_at_night", False)
    wakeup_count_goal = night_patterns.get("wake_up_count", "0")
    wakeups = int(log.get("woke_up_times", 0))
    # If user says they usually wake up, be more lenient
    if wakes_up_at_night:
        if str(wakeups) == str(wakeup_count_goal):
            score += 100 * weights["wakeups"]
        elif abs(wakeups - int(wakeup_count_goal if wakeup_count_goal.isdigit() else 1)) == 1:
            score += 70 * weights["wakeups"]
        else:
            score += 30 * weights["wakeups"]
    else:
        if wakeups == 0:
            score += 100 * weights["wakeups"]
        elif wakeups == 1:
            score += 70 * weights["wakeups"]
        else:
            score += 30 * weights["wakeups"]

    # --- 4. Morning Energy (not personalized yet) ---
    energy_options = log.get("woke_up_feeling", ["😐 Okay"])
    energy = energy_options[0] if energy_options else "😐 Okay"
    if energy in ["💪 Energized", "🙂 Refreshed", "Motivated"]:
        score += 100 * weights["energy"]
    elif energy in ["😐 Okay", "😐 Meh"]:
        score += 70 * weights["energy"]
    else:
        score += 30 * weights["energy"]

    # --- 5. Schedule Consistency (personalized to usual_bedtime) ---
    usual_bedtime = sleep_habits.get("usual_bedtime", "23:00")
    try:
        log_bedtime = log.get("bed_time", usual_bedtime)
        log_bedtime_dt = datetime.strptime(log_bedtime, "%H:%M")
        usual_bedtime_dt = datetime.strptime(usual_bedtime, "%H:%M")
        bedtime_diff = abs((log_bedtime_dt - usual_bedtime_dt).total_seconds() / 60)
    except Exception:
        bedtime_diff = consistency
    if bedtime_diff <= 15:
        score += 100 * weights["consistency"]
    elif bedtime_diff <= 30:
        score += 70 * weights["consistency"]
    else:
        score += 30 * weights["consistency"]

    # --- 6. Sleep Efficiency ---
    time_in_bed_hours = float(log.get("time_in_bed", hours if hours > 0 else 8))
    efficiency = (hours / time_in_bed_hours) * 100 if time_in_bed_hours > 0 else 0
    if efficiency >= 90:
        score += 100 * weights["efficiency"]
    elif efficiency >= 75:
        score += 70 * weights["efficiency"]
    else:
        score += 30 * weights["efficiency"]

    # --- 7. Sleep Environment (not personalized yet) ---
    environment_factors = log.get("sleep_environment", [])
    score += min(len(environment_factors), 5) / 5 * 100 * weights["environment"]  # max 10% of score

    # --- 8. Pre-Bed Stress (not personalized yet) ---
    stress_level_options = log.get("mental_state", ["Neutral"])
    stress_level = stress_level_options[0] if stress_level_options else "Neutral"
    if stress_level == "Relaxed":
        score += 100 * weights["stress"]
    elif stress_level == "Neutral":
        score += 60 * weights["stress"]
    else:
        score += 20 * weights["stress"]

    return int(min(score, 100))

# --- NEW: Use onboarding for goal display ---
def get_user_goal_for_ai(user_profile):
    onboarding = user_profile.get('onboarding', {}) if user_profile else {}
    goal_map = {
        "7+_hours": "Sleep 7+ hours",
        "no_caffeine": "No caffeine after 6pm",
        "log_daily": "Log my sleep daily",
        "bed_before_11": "Go to bed before 11pm",
        "wake_consistent": "Wake up at the same time",
        "custom": onboarding.get('goal_custom', 'Custom goal')
    }
    if onboarding.get('goal'):
        if onboarding['goal'] == 'custom':
            return onboarding.get('goal_custom', 'Custom goal')
        return goal_map.get(onboarding['goal'], onboarding['goal'])
    # fallback legacy
    return user_profile.get('goals', {}).get('primary_goal', 'improve sleep')

def get_user_struggle_for_ai(user_profile):
    onboarding = user_profile.get('onboarding', {}) if user_profile else {}
    struggle_map = {
        "falling_asleep": "Falling asleep",
        "waking_up": "Waking up during the night",
        "waking_early": "Waking up too early",
        "consistency": "Staying consistent"
    }
    if onboarding.get('struggle'):
        return struggle_map.get(onboarding['struggle'], onboarding['struggle'])
    return None

# Patch generate_gpt_suggestion to use new goal/struggle
def generate_gpt_suggestion(score, log=None, user_profile=None):
    if not openai.api_key or not log or not user_profile:
        if score >= 90:
            return "Excellent! Maintain your routine and avoid screens before bed."
        elif score >= 75:
            return "Good! Try to sleep a bit earlier for even better rest."
        else:
            return "You might benefit from cutting late-night screen time or adjusting your sleep schedule."
    try:
        user_goal = get_user_goal_for_ai(user_profile)
        user_struggle = get_user_struggle_for_ai(user_profile)
        prompt = (
            f"User's sleep score: {score}\n"
            f"User's main sleep goal: {user_goal}\n"
            f"User's biggest struggle: {user_struggle}\n"
            f"Sleep log summary: {log}\n"
            "Write a short, friendly, and practical suggestion (1-2 sentences) to help the user improve their sleep, referencing their score, goal, and struggle."
        )
        response = openai.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": "You are a helpful sleep coach."},
                      {"role": "user", "content": prompt}],
            max_tokens=60,
            temperature=0.7,
        )
        content = response.choices[0].message.content
        if content:
            return content.strip()
        else:
            return "(AI suggestion unavailable: No content returned from OpenAI.)"
    except Exception as e:
        return f"(AI suggestion unavailable: {e})"

# --- Routing Logic ---
def set_page(page):
    st.session_state.page = page
    st.rerun()

def sync_page_from_query_params():
    """Read page from URL query params and update session_state."""
    params = st.query_params
    page_from_url = params.get("page")
    if page_from_url:
        st.session_state.page = page_from_url
        params.clear()

page = st.session_state.get('page', 'login')

# --- Protected Content ---
if not st.session_state.logged_in:
    # --- Auth Page State ---
    if 'auth_page' not in st.session_state:
        st.session_state.auth_page = 'login'

    st.title("Welcome to SleepAId 🌙")
    st.markdown("""
    <style>
    .stForm {
        max-width: 700px;
        margin: 0 auto;
        background:rgb(0, 0, 0);
        padding: 2rem 2rem 1.5rem 2rem;
        border-radius: 18px;
        box-shadow: 0 4px 24px rgba(0,0,0,0.10);
        position: relative;
    }
    .stTextInput > div > input, .stPasswordInput > div > input {
        font-size: 1.1rem;
        padding: 0.75rem 1rem;
        border-radius: 8px;
        background:rgb(100, 98, 105) !important;
        color: #fff !important;
        border: 1px solid #8E05C2 !important;
        box-shadow: 0 0 0 2px rgba(192, 132, 252, 0.35) !important;
        transition: box-shadow 0.18s, border 0.18s;
    }
    .stTextInput > div > input:focus, .stPasswordInput > div > input:focus {
        background: #47424F !important;
        border: 1.5px solid #8E05C2 !important;
        box-shadow: 0 0 0 3px rgba(192, 132, 252, 0.55) !important;
    }
    .stButton > button {
        width: 30%;
        font-size: 1.1rem;
        padding: 0rem 0;
        border-radius: 8px;
        background: linear-gradient(135deg, #8E05C2 0%, #C084FC 100%);
        color: #fff;
        font-weight: 600;
    }
    .auth-topright {
        position: fixed;
        top: 1.2rem;
        right: 1.5rem;
        z-index: 1002;
        margin: 0;
        padding: 0;
    }
    .auth-topright button {
        min-width: unset !important;
        width: auto !important;
        padding: 0.35rem 1.1rem !important;
        font-size: 1rem !important;
        border-radius: 7px !important;
        background: #35323A !important;
        color: #C084FC !important;
        border: 1.5px solid #C084FC !important;
        font-weight: 600;
        box-shadow: none !important;
        transition: background 0.18s, color 0.18s;
    }
    .auth-topright button:hover {
        background: #C084FC !important;
        color: #232026 !important;
        border-color: #8E05C2 !important;
    }
    </style>
    """, unsafe_allow_html=True)

    # --- Login Page ---
    if st.session_state.auth_page == 'login':
        with st.form("login_form"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Login")
            if submitted:
                login(email, password)
        # Sign Up button beneath the form
        st.markdown("<div style='height: 0rem;'></div>", unsafe_allow_html=True)
        if st.button("Sign Up", key="go_signup"):
            st.session_state.auth_page = 'signup'
            st.rerun()     
    # --- Sign Up Page --
    elif st.session_state.auth_page == 'signup':
        # Top-right Back to Login button
        st.markdown('<div class="auth-topright">', unsafe_allow_html=True)
        if st.button("Back to Login", key="go_login"):
            st.session_state.auth_page = 'login'
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)
        with st.form("signup_form"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            confirm_password = st.text_input("Confirm Password", type="password")
            submitted = st.form_submit_button("Create Account")
            if submitted:
                if password == confirm_password:
                    signup(email, password)
                else:
                    st.error("Passwords do not match.")
else:
    # --- Main App (when logged in) ---
    # Sync page from URL first, as links will set query params
    sync_page_from_query_params()
    page = st.session_state.get('page', 'dashboard')
    
    # Handle actions from query params, like logout
    params = st.query_params
    if params.get("action") == ["logout"]:
        logout()

    with st.sidebar:
        # 1. Sidebar Logo (optional)
        logo_path = os.path.join(ASSETS_DIR, "Moon.svg")
        try:
            st.markdown(
                f"""
                <div style='display: flex; justify-content: center; align-items: center; width: 100%;'>
                    <img src='data:image/svg+xml;base64,{get_image_as_base64(logo_path)}' width='50'>
                </div>
                """,
                unsafe_allow_html=True
            )
        except Exception:
            pass

        st.markdown("---")  # Optional: a divider

        # 2. Home Button (with optional icon above)
        if st.button("Home"):
            st.session_state.page = "dashboard"
            st.rerun()

        # 3. Me Button (with optional icon above)
        if st.button("Profile"):
            st.session_state.page = "profile"
            st.rerun()

        st.markdown("---")  # Optional: another divider

        # 4. Logout Button (with optional icon above)
        if st.button("Logout"):
            logout()

    # --- Onboarding / Main App Logic ---
    user_profile = get_user_profile(st.session_state.user_uid)
    onboarding_complete = user_profile is not None and user_profile.get("onboarding_complete", False)
    

    if not onboarding_complete:
        show_onboarding_form()
        st.stop() # Stop execution to prevent dashboard from showing

    # --- DASHBOARD ---
    elif page == "dashboard":
        # --- Main Content ---
        logs = load_user_logs(st.session_state.user_uid)
        # --- Calculate Streaks ---
        user_timezone = user_profile.get('personal_info', {}).get('timezone', 'UTC') if user_profile else 'UTC'
        current_streak, longest_streak = calculate_streaks(logs)
        # --- Streak Badge ---
        streak_emoji = '🔥' if current_streak >= 3 else '🌙'
        streak_badge_html = f"""
        <div style='text-align:center; margin-bottom:1rem;'>
            <span style='font-size:2.2rem;'>{streak_emoji}</span><br>
            <span style='color:#A78BFA; font-size:1.3rem; font-weight:600;'>Current Streak: {current_streak} day{'s' if current_streak != 1 else ''}</span><br>
            <span style='color:#CCC8CF; font-size:1.1rem;'>Longest Streak: {longest_streak} day{'s' if longest_streak != 1 else ''}</span>
        </div>
        """
        st.markdown(streak_badge_html, unsafe_allow_html=True)
        # --- Celebrate new streak milestones ---
        if current_streak in [3, 7, 14, 30, 100]:
            st.success(f"🎉 Congrats! {current_streak}-day streak! Keep it going!")
        
        # --- Calculate Today's Score and Change ---
        today_score = 0
        change_percent = 0
        if logs:
            consistency_diff = 0
            # Calculate consistency if there's a previous log to compare to
            if len(logs) > 1:
                try:
                    latest_bedtime = datetime.strptime(logs[0].get("bed_time", "00:00"), "%H:%M")
                    previous_bedtime = datetime.strptime(logs[1].get("bed_time", "00:00"), "%H:%M")
                    consistency_diff = abs((latest_bedtime - previous_bedtime).total_seconds() / 60)
                except (ValueError, KeyError):
                    consistency_diff = 0 # Handle potential missing or malformed data

            today_score = calculate_sleep_score(logs[0], user_profile, consistency_diff)

            # Calculate percentage change from the previous day's score
            if len(logs) > 1:
                previous_score = calculate_sleep_score(logs[1], user_profile, 0) # Use 0 consistency for prev day
                if previous_score > 0:
                    change_percent = int(((today_score - previous_score) / previous_score) * 100)
                elif today_score > 0:
                    change_percent = 100 # From 0 to a positive score

        # --- Centered Logo Header ---
        logo_path = os.path.join(ASSETS_DIR, "sleepaid_text.svg")
        if os.path.exists(logo_path):
            st.markdown(
                f"""
                <div style='display: flex; justify-content: center; margin-bottom: 2rem;'>
                    <img src='data:image/svg+xml;base64,{get_image_as_base64(logo_path)}' width='350'>
                </div>
                """,
                unsafe_allow_html=True
            )

        # --- Today's Sleep Score (Displayed OUTSIDE the grey box) ---
        arrow = "↓" if change_percent < 0 else "↑"
        change_color = "#E5545B" if change_percent < 0 else "#5EE5A1"

        score_card_html = f"""
        <div style='text-align: center; margin-bottom: 2rem;'>
            <div style="font-size: 1.1rem; color: #CCC8CF; margin-bottom: 0.5rem;">Today's Sleep Score</div>
            <div class='score-row' style='display: flex; align-items: baseline; justify-content: center; gap: 1rem;'>
                <h1 style='font-size: 8rem; font-weight: 700; margin: 0; line-height: 1;'>{today_score}</h1>
                <p style='color: {change_color}; font-size: 2rem; font-weight: 600; margin: 0 0 1rem 0;'>{arrow} {abs(change_percent)}%</p>
            </div>
        </div>
        """
        st.markdown(score_card_html, unsafe_allow_html=True)
        
        # --- Tabs for different views (INSIDE the grey box) ---
        st.markdown("<div class='dashboard-tabs-container'>", unsafe_allow_html=True)
        with st.container(border=True):
            tabs = ["Metrics", "GPT Suggestion", "Last 7 Days"]
            if 'active_tab' not in st.session_state:
                st.session_state.active_tab = None

            cols = st.columns(len(tabs))
            for i, tab in enumerate(tabs):
                with cols[i]:
                    if st.button(tab, key=f"tab_{tab}", use_container_width=True):
                        # Toggle behavior: if same tab is clicked, close it.
                        if st.session_state.get('active_tab') == tab:
                            st.session_state.active_tab = None
                        else:
                            st.session_state.active_tab = tab
                        st.rerun()

            # --- Display content based on active tab ---
            active_tab = st.session_state.get("active_tab") # Can be None
            if active_tab:
                st.markdown("<div style='padding-top: 1.5rem;'>", unsafe_allow_html=True)

                if active_tab == "Metrics":
                    if logs:
                        latest_log = logs[0]
                        efficiency_val = f"{latest_log.get('sleep_efficiency', 0):.0f}%" if 'sleep_efficiency' in latest_log else "N/A"
                        latency_val = f"{latest_log.get('time_to_fall_asleep', 'N/A')} min" if 'time_to_fall_asleep' in latest_log else "N/A"

                        col1, col2, col3 = st.columns(3)
                        with col1:
                            st.metric("Hours Slept", f"{latest_log.get('hours_slept', 'N/A')}")
                        with col2:
                            st.metric("Sleep Efficiency", efficiency_val)
                        with col3:
                            st.metric("Time to Fall Asleep", latency_val)
                    else:
                        st.info("Log your sleep to see your metrics here.")

                elif active_tab == "GPT Suggestion":
                    st.markdown("<h4 style='text-align: center; color: #CCC8CF;'>AI-Powered Insight</h4>", unsafe_allow_html=True)
                    user_usage = get_user_usage(st.session_state.user_uid) or {}
                    if user_usage.get("messages", 0) >= 100:
                        st.warning("You've hit your monthly message limit.")
                        suggestion = "(AI suggestion unavailable: message limit reached.)"
                    else:
                        suggestion = generate_gpt_suggestion(today_score, logs[0] if logs else None, user_profile)
                        increment_user_usage(st.session_state.user_uid)
                    st.markdown(f"<p style='text-align: center; font-size: 1.1rem; padding: 0 1rem;'>{suggestion}</p>", unsafe_allow_html=True)

                elif active_tab == "Last 7 Days":
                    st.markdown("<h4 style='text-align: center; margin-bottom: 1.5rem; color: #C084FC; font-weight: 600;'>7-Day Sleep Score Trend</h4>", unsafe_allow_html=True)
                    today = datetime.now()
                    date_range = [today - timedelta(days=i) for i in range(6, -1, -1)]
                    scores_by_date = {log['date']: calculate_sleep_score(log, user_profile, 0) for log in logs}
                    trend_data = []
                    for day in date_range:
                        date_str = day.strftime('%Y-%m-%d')
                        score = scores_by_date.get(date_str, 0)
                        trend_data.append({'day': get_day_label(day), 'score': score})
                    
                    df = pd.DataFrame(trend_data)
                    
                    fig = go.Figure()
                    bar_colors = ['#A78BFA' if s > 0 else 'rgba(0,0,0,0)' for s in df['score']]
                    fig.add_trace(go.Bar(
                        x=df.index,
                        y=df['score'],
                        marker_color=bar_colors,
                        marker_line_width=0,
                        width=0.6,
                        customdata=df['day'],
                        hovertemplate='<b>%{customdata}</b><br>Score: %{y}<extra></extra>'
                    ))
                    fig.update_layout(
                        xaxis=dict(showgrid=False, showline=False, zeroline=False, tickfont=dict(color='#CCC8CF', size=14), tickmode='array', tickvals=df.index, ticktext=df['day']),
                        yaxis=dict(showgrid=False, showline=False, zeroline=False, showticklabels=False, range=[0, 105]),
                        plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
                        margin=dict(l=0, r=0, t=0, b=0), bargap=0.2, height=150
                    )
                    fig.update_traces(marker_cornerradius=8)
                    st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
                
                st.markdown("</div>", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

        # --- Log Sleep Button ---
        st.markdown('<div class="log-sleep-button-container" style="margin-top: 1.5rem;">', unsafe_allow_html=True)
        if st.button("🌙 Log Today's Sleep", use_container_width=True):
            set_page("log")
        st.markdown('</div>', unsafe_allow_html=True)

        # --- Top Right Avatar ---
        avatar_path = os.path.join(AVATAR_DIR, f"{st.session_state.user_uid}.png")
        if os.path.exists(avatar_path):
            avatar_img = f"data:image/png;base64,{get_image_as_base64(avatar_path)}"
            avatar_html = f"""
            <div style='position: fixed; top: 1.2rem; right: 1.5rem; z-index: 1002;'>
                <img src='{avatar_img}' width='48' height='48' style='border-radius:50%; border:2px solid #C084FC; background:#232026;'/>
            </div>
            """
        else:
            initials = ''.join([x[0] for x in user_profile.get('personal_info', {}).get('name', 'User').split()]) if user_profile else 'U'
            avatar_html = f"""
            <div style='position: fixed; top: 1.2rem; right: 1.5rem; z-index: 1002;'>
                <div style='width:48px; height:48px; border-radius:50%; background:linear-gradient(135deg,#8E05C2 0%,#6A00AC 100%); color:#fff; display:flex; align-items:center; justify-content:center; font-size:1.5rem; font-weight:700; border:2px solid #C084FC;'>{initials}</div>
            </div>
            """
        st.markdown(avatar_html, unsafe_allow_html=True)

    # --- Profile Page (with sidebar for logged-in users) ---
    elif page == "profile":
        user_name = user_profile.get('personal_info', {}).get('name', 'User') if (user_profile and isinstance(user_profile, dict)) else 'User'
        initials = ''.join([x[0] for x in user_name.split()]) if user_name else 'U'
        initials = initials.upper()
        logs = load_user_logs(st.session_state.user_uid)
        sleeps_logged = len(logs)
        avg_score = int(sum([calculate_sleep_score(log, user_profile, 0) for log in logs]) / sleeps_logged) if sleeps_logged else 0
        # --- Profile Editing State ---
        if 'editing_profile' not in st.session_state:
            st.session_state.editing_profile = False

        if st.session_state.editing_profile:
            st.markdown("## Edit Profile")
            personal_info = user_profile.get('personal_info', {}) if user_profile else {}
            sleep_patterns = user_profile.get('sleep_patterns', {}) if user_profile else {}
            lifestyle_support = user_profile.get('lifestyle_support', {}) if user_profile else {}
            uid = st.session_state.user_uid
            avatar_path = os.path.join(AVATAR_DIR, f"{uid}.png")
            col1, col2, col3 = st.columns([1, 1, 1])
            with col1:
                if os.path.exists(avatar_path):
                    st.image(avatar_path, width=100)
                else:
                    st.image("https://ui-avatars.com/api/?name=User", width=100)
            with col2:
                if st.button("Change Avatar"):
                    st.session_state.show_avatar_modal = True
            with col3:
                if os.path.exists(avatar_path):
                    if st.button("Remove"):
                        os.remove(avatar_path)
                        st.success("Avatar removed!")
                        time.sleep(0.5)
                        st.rerun()
            if st.session_state.get("show_avatar_modal", False):
                with st.expander("Upload a new avatar", expanded=True):
                    uploaded_file = st.file_uploader("Choose a new avatar", type=["png", "jpg", "jpeg"])
                    if uploaded_file:
                        with open(avatar_path, "wb") as f:
                            f.write(uploaded_file.read())
                        st.success("Avatar updated!")
                        st.session_state.show_avatar_modal = False
                        time.sleep(0.5)
                        st.rerun()
                    if st.button("Close"):
                        st.session_state.show_avatar_modal = False
            with st.form("edit_profile_form"):
                st.markdown("### Personal Info")
                first_name = st.text_input("First Name", value=personal_info.get('first_name', ''))
                age = st.text_input("Age", value=str(personal_info.get('age', '')))
                gender = st.selectbox("Gender (optional)", ["", "Male", "Female", "Other"], index=["", "Male", "Female", "Other"].index(personal_info.get('gender', '')))
                timezone_list = pytz.all_timezones
                timezone = st.selectbox("Time Zone", timezone_list, index=timezone_list.index(personal_info.get('timezone', 'UTC')) if personal_info.get('timezone', 'UTC') in timezone_list else 0)
                st.markdown("### Sleep Patterns")
                struggle = st.selectbox("What's your biggest sleep struggle?", ["Falling asleep", "Waking up during the night", "Waking up too early", "Staying consistent"], index=["Falling asleep", "Waking up during the night", "Waking up too early", "Staying consistent"].index(sleep_patterns.get('struggle', 'Falling asleep')))
                goal = st.selectbox("What's your main sleep goal?", ["Sleep 7+ hours", "No caffeine after 6pm", "Log my sleep daily", "Go to bed before 11pm", "Wake up at the same time", "Custom goal"], index=["Sleep 7+ hours", "No caffeine after 6pm", "Log my sleep daily", "Go to bed before 11pm", "Wake up at the same time", "Custom goal"].index(sleep_patterns.get('goal', 'Sleep 7+ hours')))
                goal_custom = ""
                if goal == "Custom goal":
                    goal_custom = st.text_input("Describe your custom sleep goal:", value=sleep_patterns.get('goal_custom', ''))
                usual_bedtime = st.time_input("What time do you usually go to bed?", value=datetime.strptime(sleep_patterns.get('usual_bedtime', '23:00'), "%H:%M").time())
                usual_wake_time = st.time_input("What time do you usually wake up?", value=datetime.strptime(sleep_patterns.get('usual_wake_time', '07:00'), "%H:%M").time())
                st.markdown("### Lifestyle & Support Preferences")
                workout = st.radio("Do you have a workout routine?", ["Yes", "No"], index=0 if lifestyle_support.get('workout', 'No') == 'Yes' else 1)
                workout_freq = 0
                if workout == "Yes":
                    workout_freq = st.number_input("How many times per week?", min_value=1, max_value=14, value=lifestyle_support.get('workout_freq', 3))
                caffeine = st.radio("Do you use caffeine?", ["Yes", "No"], index=0 if lifestyle_support.get('caffeine', 'No') == 'Yes' else 1)
                caffeine_time = ""
                if caffeine == "Yes":
                    caffeine_time = st.time_input("Time of last caffeine?", value=datetime.strptime(lifestyle_support.get('caffeine_time', '15:00'), "%H:%M").time())
                phone_use = st.radio("Do you use your phone at night?", ["Yes", "No"], index=0 if lifestyle_support.get('phone_use', 'Yes') == 'Yes' else 1)
                support_pref = st.text_area("What would you like SleepAid to help with most?", value=lifestyle_support.get('support_pref', ''))
                submitted = st.form_submit_button("Save Changes")
                cancel = st.form_submit_button("Cancel")
            if submitted:
                errors = []
                if not (first_name or "").strip():
                    errors.append("First name is required.")
                try:
                    age_val = int(age or "")
                    if age_val <= 0 or age_val > 120:
                        errors.append("Age must be a positive number less than 120.")
                except Exception:
                    errors.append("Please enter a valid number for age.")
                if not timezone:
                    errors.append("Time zone is required.")
                if goal == "Custom goal" and not (goal_custom or "").strip():
                    errors.append("Please enter your custom goal.")
                if not usual_bedtime:
                    errors.append("Usual bedtime is required.")
                if not usual_wake_time:
                    errors.append("Usual wake time is required.")
                if errors:
                    for err in errors:
                        st.error(err)
                    st.stop()
                updated_profile = user_profile.copy() if user_profile and isinstance(user_profile, dict) else {}
                updated_profile['personal_info'] = {
                    "first_name": (first_name or "").strip(),
                    "age": age_val,
                    "gender": gender,
                    "timezone": timezone,
                }
                updated_profile['sleep_patterns'] = {
                    "struggle": struggle,
                    "goal": goal,
                    "goal_custom": (goal_custom or "").strip() if goal == "Custom goal" else "",
                    "usual_bedtime": usual_bedtime.strftime("%H:%M") if usual_bedtime else '',
                    "usual_wake_time": usual_wake_time.strftime("%H:%M") if usual_wake_time else '',
                }
                updated_profile['lifestyle_support'] = {
                    "workout": workout,
                    "workout_freq": workout_freq if workout == "Yes" else 0,
                    "caffeine": caffeine,
                    "caffeine_time": caffeine_time.strftime("%H:%M") if caffeine == "Yes" and caffeine_time else '',
                    "phone_use": phone_use,
                    "support_pref": (support_pref or '').strip(),
                }
                updated_profile['onboarding_complete'] = True
                updated_profile['updated_at'] = datetime.now().isoformat()
                if save_user_profile(st.session_state.user_uid, updated_profile):
                    st.success("✅ Profile updated!")
                    st.session_state.editing_profile = False
                    time.sleep(0.5)
                    st.rerun()
            elif cancel:
                st.session_state.editing_profile = False
                st.rerun()
            st.stop()

        # --- Profile Card with Edit Profile Button truly inside ---
        personal_info = user_profile.get('personal_info', {}) if user_profile else {}
        first_name = (personal_info.get('first_name') or '').strip()
        display_name = first_name if first_name else 'User'
        initials = display_name[0].upper() if display_name else 'U'
        avatar_path = os.path.join(AVATAR_DIR, f"{st.session_state.user_uid}.png")
        if os.path.exists(avatar_path):
            avatar_img_html = f"<img src='data:image/png;base64,{get_image_as_base64(avatar_path)}' width='64' style='border-radius:50%;'/>"
        else:
            avatar_img_html = f"<div class='me-avatar'>{initials}</div>"
            

        st.markdown(
            f'''
            <div class="me-card">
                {avatar_img_html}
                <div class="me-info">
                    <h3>{display_name}</h3>
                    <div class="me-metrics">
                        <div>
                            <span class="me-metric-label">Average Sleep Score</span>
                            <span class="me-metric-value">{avg_score}</span>
                        </div>
                        <div>
                            <span class="me-metric-label">Sleeps Logged</span>
                            <span class="me-metric-value">{sleeps_logged}</span>
                        </div>
                    </div>
                </div>
            </div>
            ''', unsafe_allow_html=True 
        )
        # Place the Edit Profile button visually inside the card using columns for alignment
        button_col, _ = st.columns([1, 5])
        with button_col:
            if st.button("Edit Profile"):
                st.session_state.editing_profile = True
                st.rerun()
        # --- Goal Card ---
        goal_icon_svg = """
        <svg width="36" height="36" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
            <circle cx="12" cy="12" r="10" stroke="#C084FC" stroke-width="2"/>
            <circle cx="12" cy="12" r="6" stroke="#C084FC" stroke-width="2"/>
            <circle cx="12" cy="12" r="2" fill="#C084FC"/>
        </svg>
        """
        # --- NEW: Use onboarding for goal display ---
        onboarding = user_profile.get('onboarding', {}) if user_profile else {}
        goal_map = {
            "7+_hours": "Sleep 7+ hours",
            "no_caffeine": "No caffeine after 6pm",
            "log_daily": "Log my sleep daily",
            "bed_before_11": "Go to bed before 11pm",
            "wake_consistent": "Wake up at the same time",
            "custom": None
        }
        user_goal = goal_map.get(onboarding.get('goal', ''), None)
        if onboarding.get('goal', '') == 'custom':
            user_goal = onboarding.get('goal_custom', 'Custom goal') if isinstance(onboarding.get('goal_custom', None), str) else 'Custom goal'
        if not user_goal:
            # fallback to legacy
            user_goal = user_profile.get('goals', {}).get('primary_goal', 'Goal not set') if user_profile and isinstance(user_profile, dict) else 'Goal not set'
        goal_status = "On Track!"

        goal_card_html = f"""
        <div class='metric-container' style='margin-top: 1rem;'>
            <div style='display: flex; align-items: center; width: 100%;'>
                <div style='margin-right: 2rem;'>{goal_icon_svg}</div>
                <div style='display: flex; flex-direction: column; flex-grow: 1; align-items: center;'>
                    <div style="font-size: 1.1rem; color: #CCC8CF;">Your Goal</div>
                    <div style="font-size: 1.3rem; font-weight: 600; color: #fff;">{user_goal}</div>
                </div>
                <div>
                    <span style="font-size: 1.1rem; color: #C084FC; font-weight: 600;">{goal_status}</span>
                    <span style='font-size: 1.1rem; color: #C084FC; margin-left: 0.2rem;'>↗</span>
                </div>
            </div>
        </div>
        """
        st.markdown(goal_card_html, unsafe_allow_html=True)

        # --- 7-Day Sleep Trend Chart ---
        st.markdown("<div class='me-page-trend-container'>", unsafe_allow_html=True)
        with st.container(border=True):
            st.markdown("<h3 style='text-align: center; margin-bottom: 1.5rem; color: #C084FC; font-weight: 600;'>7-Day Sleep Trend</h3>", unsafe_allow_html=True)

            # 1. Prepare data for the last 7 days, ensuring correct chronological order
            today = datetime.now()
            date_range = [today - timedelta(days=i) for i in range(6, -1, -1)] # Past to present
            
            day_order = [get_day_label(day) for day in date_range]
            scores_by_date = {log['date']: calculate_sleep_score(log, user_profile, 0) for log in logs}

            trend_data = []
            for day in date_range:
                date_str = day.strftime('%Y-%m-%d')
                score = scores_by_date.get(date_str, 0)
                trend_data.append({'day': get_day_label(day), 'score': score})

            df = pd.DataFrame(trend_data)

            # 2. Calculate summary stats (from logged days only)
            logged_scores = [s for s in df['score'] if s > 0]
            if logged_scores:
                avg_score_7d = int(statistics.mean(logged_scores))
                best_score_7d = int(max(logged_scores))
                low_score_7d = int(min(logged_scores))
            else:
                avg_score_7d, best_score_7d, low_score_7d = 0, 0, 0

            # 3. Create the Plotly chart
            fig = go.Figure()
            
            # Make bars for unlogged days invisible
            bar_colors = ['#A78BFA' if s > 0 else 'rgba(0,0,0,0)' for s in df['score']]

            fig.add_trace(go.Bar(
                x=df.index, # Use the numeric index for plotting
                y=df['score'],
                marker_color=bar_colors,
                marker_line_width=0,
                width=0.6,
                customdata=df['day'], # Pass day initials for hover
                hovertemplate='<b>%{customdata}</b><br>Score: %{y}<extra></extra>'
            ))

            fig.update_layout(
                xaxis=dict(
                    showgrid=False, 
                    showline=False, 
                    zeroline=False, 
                    tickfont=dict(color='#CCC8CF', size=14),
                    tickmode='array', # Use array mode for custom labels
                    tickvals=df.index, # Set ticks at the index positions
                    ticktext=df['day']  # Use day initials as the labels
                ),
                yaxis=dict(showgrid=False, showline=False, zeroline=False, showticklabels=False, range=[0, 105]),
                plot_bgcolor='rgba(0,0,0,0)',
                paper_bgcolor='rgba(0,0,0,0)',
                margin=dict(l=0, r=0, t=0, b=0),
                bargap=0.2,
                height=200
            )
            fig.update_traces(marker_cornerradius=8)

            st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
            st.markdown("<hr style='border-color: #4A4A4A; margin-top: 1rem; margin-bottom: 1rem;'>", unsafe_allow_html=True)

            col1, col2, col3 = st.columns(3)
            with col1:
                st.markdown(f"<div style='text-align: center;'><div style='font-size: 1.8rem; font-weight: 600; color: #A78BFA;'>{avg_score_7d}</div><div style='color: #CCC8CF;'>Avg</div></div>", unsafe_allow_html=True)
            with col2:
                st.markdown(f"<div style='text-align: center;'><div style='font-size: 1.8rem; font-weight: 600; color: #A78BFA;'>{best_score_7d}</div><div style='color: #CCC8CF;'>Best</div></div>", unsafe_allow_html=True)
            with col3:
                st.markdown(f"<div style='text-align: center;'><div style='font-size: 1.8rem; font-weight: 600; color: #A78BFA;'>{low_score_7d}</div><div style='color: #CCC8CF;'>Low</div></div>", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

        # --- Sleep Log History Table (Full History) ---
        st.markdown("<h4 style='color: #C084FC; font-weight: 700; margin-bottom: 1rem;'>Sleep Log History</h4>", unsafe_allow_html=True)
        if logs:
            # Prepare DataFrame
            history_data = []
            for log in logs:
                history_data.append({
                    "Date": log.get("date", "-"),
                    "Hours Slept": log.get("hours_slept", "-"),
                    "Bed Time": log.get("bed_time", "-"),
                    "Wake Time": log.get("wake_time", "-"),
                    "Time to Fall Asleep (min)": log.get("time_to_fall_asleep", "-"),
                    "Wakeups": log.get("woke_up_times", "-"),
                    "Quality": log.get("quality_rating", "-"),
                    "Notes": log.get("notes", "")[:60]  # Truncate long notes
                })
            df_history = pd.DataFrame(history_data)
            # Sort by date descending if possible
            try:
                df_history["Date"] = pd.to_datetime(df_history["Date"], errors="coerce")
                df_history = df_history.sort_values("Date", ascending=False)
                df_history["Date"] = df_history["Date"].dt.strftime("%Y-%m-%d")
            except Exception:
                pass
            # --- Download as CSV button (restyled) ---
            st.markdown("""
                <style>
                .csv-download-btn-container {
                    width: 100%;
                    display: flex;
                    justify-content: flex-start;
                    margin-top: 0.5rem;
                }
                .csv-download-btn-container .stDownloadButton > button {
                    width: 40% !important;
                    min-width: 120px;
                    background: linear-gradient(135deg, #8E05C2 0%, #C084FC 100%) !important;
                    color: #fff !important;
                    border: none !important;
                    border-radius: 8px !important;
                    font-weight: 600 !important;
                    font-size: 1.1rem !important;
                    padding: 0.6rem 0 !important;
                    margin: 0 !important;
                    box-shadow: none !important;
                    transition: background 0.18s, color 0.18s !important;
                }
                .csv-download-btn-container .stDownloadButton > button:hover {
                    background: linear-gradient(135deg, #C084FC 0%, #8E05C2 100%) !important;
                    color: #232026 !important;
                }
                </style>
                <div class='csv-download-btn-container'>
            """, unsafe_allow_html=True)
            csv = df_history.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="Download as CSV",
                data=csv,
                file_name="sleep_log_history.csv",
                mime="text/csv",
                key="csv_download_button"
            )
            st.markdown("</div>", unsafe_allow_html=True)
            st.dataframe(df_history, use_container_width=True, hide_index=True)
        else:
            st.info("No sleep logs yet. Log your sleep to see your history here!")

        # --- Personalized Insights Block 
        st.markdown("<h4 style='color: #C084FC; font-weight: 700; margin-bottom: 1rem;'>Personalized Insights</h4>", unsafe_allow_html=True)

        # Sleep Consistency: average bedtime/wake time difference over last 7 logs
        if len(logs) > 1:
            bedtime_diffs = []
            waketime_diffs = []
            for i in range(1, min(7, len(logs))):
                try:
                    prev_bed = datetime.strptime(logs[i]['bed_time'], "%H:%M")
                    curr_bed = datetime.strptime(logs[i-1]['bed_time'], "%H:%M")
                    bedtime_diffs.append(abs((curr_bed - prev_bed).total_seconds() / 60))
                    prev_wake = datetime.strptime(logs[i]['wake_time'], "%H:%M")
                    curr_wake = datetime.strptime(logs[i-1]['wake_time'], "%H:%M")
                    waketime_diffs.append(abs((curr_wake - prev_wake).total_seconds() / 60))
                except Exception:
                    continue
            avg_bedtime_consistency = int(statistics.mean(bedtime_diffs)) if bedtime_diffs else 0
            avg_waketime_consistency = int(statistics.mean(waketime_diffs)) if waketime_diffs else 0
        else:
            avg_bedtime_consistency = 0
            avg_waketime_consistency = 0
        st.markdown(f"<b>Sleep Consistency:</b> Your average bedtime difference is <span style='color:#A78BFA'>{avg_bedtime_consistency} min</span> and wake time difference is <span style='color:#A78BFA'>{avg_waketime_consistency} min</span> over the last 7 days.", unsafe_allow_html=True)

        # Goal Progress: visualize progress toward primary sleep goal
        sleep_habits = user_profile.get('sleep_habits', {}) if user_profile else {}
        goal = sleep_habits.get('sleep_duration_goal', '7-8 hours')
        goal_ranges = {
            "<6 hours": (0, 6),
            "6-7 hours": (6, 7),
            "7-8 hours": (7, 8),
            "8+ hours": (8, 24),
        }
        min_goal, max_goal = goal_ranges.get(goal, (7, 8))
        # Calculate % of last 7 logs within goal
        logs_in_goal = 0
        for log in logs[:7]:
            hours = float(log.get("hours_slept", 0))
            if min_goal <= hours <= max_goal:
                logs_in_goal += 1
        percent_in_goal = int((logs_in_goal / min(7, len(logs))) * 100) if logs else 0
        st.markdown(f"<b>Goal Progress:</b> <span style='color:#A78BFA'>{percent_in_goal}%</span> of your last 7 nights met your sleep duration goal (<b>{goal}</b>).", unsafe_allow_html=True)

        # AI Insights: summarize trends or recurring issues
        # We'll use a simple rule-based summary for now
        if logs:
            last7 = logs[:7]
            avg_hours = statistics.mean([float(log.get("hours_slept", 0)) for log in last7])
            avg_latency = statistics.mean([int(log.get("time_to_fall_asleep", 0)) for log in last7])
            avg_wakeups = statistics.mean([int(log.get("woke_up_times", 0)) for log in last7])
            energy_counts = {}
            for log in last7:
                for log_item in last7:
                    woke_up_feeling = log_item.get("woke_up_feeling", [])
                    if isinstance(woke_up_feeling, str):
                        # If it's a string, treat as a single feeling
                        feelings = [woke_up_feeling]
                    else:
                        # Otherwise, assume it's a list
                        feelings = woke_up_feeling
                    for feeling in feelings:
                        energy_counts[feeling] = energy_counts.get(feeling, 0) + 1
                most_common_energy = max(energy_counts.items(), key=lambda x: x[1])[0] if energy_counts else "N/A"
                ai_summary = f"You averaged <b>{avg_hours:.1f} hours</b> of sleep, took <b>{avg_latency:.0f} min</b> to fall asleep, and woke up <b>{avg_wakeups:.1f} times</b> per night. Most common morning feeling: <b>{most_common_energy}</b>."
            else:
                ai_summary = "Not enough data for insights yet. Log more sleep!"
            st.markdown(f"<b>AI Insights:</b> {ai_summary}", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

        # --- Streak Badge on Profile ---
        # Calculate current streak and longest streak
        def calculate_streaks(logs):
            from datetime import datetime, timedelta

            if not logs:
                return 0, 0

            # Sort logs by date descending
            sorted_logs = sorted(logs, key=lambda x: x.get("date", ""), reverse=True)
            streak = 0
            longest = 0
            prev_date = None

            for log in sorted_logs:
                log_date_str = log.get("date")
                if not log_date_str:
                    continue
                try:
                    log_date = datetime.strptime(log_date_str, "%Y-%m-%d")
                except Exception:
                    continue

                if prev_date is None:
                    streak = 1
                else:
                    if prev_date - log_date == timedelta(days=1):
                        streak += 1
                    elif prev_date - log_date > timedelta(days=1):
                        break  # streak ended

                prev_date = log_date

            # Now, calculate the longest streak
            longest = 0
            temp_streak = 1
            for i in range(1, len(sorted_logs)):
                try:
                    d1 = datetime.strptime(sorted_logs[i-1].get("date", ""), "%Y-%m-%d")
                    d2 = datetime.strptime(sorted_logs[i].get("date", ""), "%Y-%m-%d")
                except Exception:
                    continue
                if (d1 - d2) == timedelta(days=1):
                    temp_streak += 1
                else:
                    if temp_streak > longest:
                        longest = temp_streak
                    temp_streak = 1
            if temp_streak > longest:
                longest = temp_streak

            return streak, longest

        current_streak, longest_streak = calculate_streaks(logs)
        streak_emoji = '🔥' if current_streak >= 3 else '🌙'
        streak_badge_html = f"""
        <div style='text-align:center; margin-bottom:1rem;'>
            <span style='font-size:2.2rem;'>{streak_emoji}</span><br>
            <span style='color:#A78BFA; font-size:1.3rem; font-weight:600;'>Current Streak: {current_streak} day{'s' if current_streak != 1 else ''}</span><br>
            <span style='color:#CCC8CF; font-size:1.1rem;'>Longest Streak: {longest_streak} day{'s' if longest_streak != 1 else ''}</span>
        </div>
        """
        st.markdown(streak_badge_html, unsafe_allow_html=True)
        # --- Celebrate new streak milestones ---
        if current_streak in [3, 7, 14, 30, 100]:
            st.success(f"🎉 Congrats! {current_streak}-day streak! Keep it going!")

    # --- Log Sleep Page ---
    elif page == "log":
        st.markdown("<h1 style='text-align: center;'>Log Today's Sleep</h1>", unsafe_allow_html=True)
        # --- Custom CSS for accent color and mobile-friendly form ---
        st.markdown("""
        <style>
            .stSelectbox > div[data-baseweb="select"] span,
            .stMultiSelect > div[data-baseweb="select"] span {
                color: #8E05C2 !important;
            }
            .stCheckbox > label > div:first-child {
                border-color: #8E05C2 !important;
            }
            .stRadio > div[role="radiogroup"] label {
                color: #8E05C2 !important;
            }
            .stButton > button {
                background: linear-gradient(90deg, #8E05C2 0%, #6A00AC 100%) !important;
                color: #fff !important;
                border-radius: 24px !important;
                font-weight: 700 !important;
                font-size: 1.1rem !important;
                padding: 0.75rem 2rem !important;
                border: none !important;
                margin-top: 0.5rem;
                transition: background 0.2s;
            }
            .stButton > button:hover {
                background: linear-gradient(90deg, #6A00AC 0%, #8E05C2 100%) !important;
                color: #fff !important;
            }
            .stButton > button.active-tab {
                color: #8E05C2;
                font-weight: 600;
                border-bottom: 2px solid #8E05C2;
            }
            .tabs-container {
                margin-top: -0rem; /* Further "attach" tabs */
                background-color: #1C191E; /* Match page background */
                padding: 0.5rem 0.5rem 0.5rem 0.5rem;
                border-radius: 0 0 16px 16px;
            }
            .content-area {
                margin-top: 0.2rem;
                padding: 1rem;
            }
            /* Outlined Log Sleep Button */
            .log-sleep-button-container {
                display: flex;
                justify-content: center;
                margin-top: 0.2rem; /* Reduced margin */
            }
            .stButton>button.log-sleep-btn {
                background: transparent !important;
                border: 2px solid #6A00AC !important;
                color: #CCC8CF !important;
                width: 100%;
                max-width: 450px;
                font-weight: 700;
            }
            .stButton>button.log-sleep-btn:hover {
                border-color: #8E05C2 !important;
                color: #8E05C2 !important;
            }
            .main-content {
                margin-top: -0.5rem;
                }
            .me-card {
                background: #232026;
                border-radius: 16px;
                padding: 1.5rem 1rem;
                display: flex;
                align-items: center;
                gap: 1.5rem;
                margin-bottom: 1.5rem;
            }
            .me-avatar {
                background: linear-gradient(135deg, #8E05C2 0%, #6A00AC 100%);
                color: #fff;
                font-size: 2.2rem;
                font-weight: 700;
                border-radius: 50%;
                width: 64px;
                height: 64px;
                display: flex;
                align-items: center;
                justify-content: center;
            }
            .me-info h3 {
                margin: 0 0 0.5rem 0;
                color: #fff;
                font-size: 1.5rem;
            }
            .me-metrics {
                display: flex;
                gap: 2rem;
            }
            .me-metric-label {
                color: #C084FC;
                font-size: 0.95rem;
                display: block;
            }
            .me-metric-value {
                color: #fff;
                font-size: 1.2rem;
                font-weight: 600;
            }
            .log-form-card {
                border-radius: 14px;
                padding: 2rem 1.5rem 1.5rem 1.5rem;
            }
        </style>
        """, unsafe_allow_html=True)

        # Form to log sleep
        with st.form(key="sleep_log_form"):
            # Hours slept dropdown
            hours_options = [f"{h:.1f}" for h in [x * 0.5 for x in range(6, 25)]]  # 3.0 to 12.0
            hours_slept = st.selectbox("How many hours did you sleep?", hours_options, index=8, help="Select your total sleep duration.")
            
            time_in_bed = st.number_input(
                "Total time spent in bed (hours)",
                min_value=0.0, max_value=24.0, value=float(hours_slept) + 0.5, step=0.5,
                help="Include time trying to fall asleep and any wake-ups."
            )

            time_to_fall_asleep = st.number_input(
                "Time to Fall Asleep (minutes)",
                min_value=0, max_value=180, value=15, step=5,
                help="Roughly how long did it take you to fall asleep?"
            )

            # Bed and wake time
            bed_time = st.time_input("What time did you go to bed?", value=time_type(23, 0))
            wake_time = st.time_input("What time did you wake up?", value=time_type(7, 0))
            
            # New multiselect for wake-up feeling
            woke_up_feeling = st.multiselect(
                "How did you feel when you woke up?",
                ["😴 Exhausted", "😐 Meh", "🙂 Refreshed", "💪 Energized"]
            )
            
            # Simplified wake-up question - always visible
            wakeup_count_str = st.selectbox(
                "How many times did you wake up last night?",
                ["0 (I didn't wake up)", "1 time", "2 times", "3+ times"]
            )
            
            quality_rating = st.selectbox(
                "How would you rate your sleep quality?",
                list(range(1, 11)),
                index=6
            )

            sleep_environment = st.multiselect(
                "Describe your sleep environment (optional)",
                ["Room was cool", "Dark", "Quiet", "No screens", "No caffeine"]
            )

            mental_state = st.multiselect(
                "Mental state before bed",
                ["Relaxed", "Neutral", "Stressed"]
            )

            # Notes text input
            notes = st.text_area("Any notes or comments about your sleep?")
            submitted = st.form_submit_button("Submit Sleep Log")

        if submitted:
            errors = []
            try:
                hours_val = float(hours_slept)
                if hours_val <= 0 or hours_val > 24:
                    errors.append("Hours slept must be between 0 and 24.")
            except Exception:
                errors.append("Please enter a valid number for hours slept.")
            if time_in_bed < hours_val or time_in_bed > 24:
                errors.append("Time in bed must be at least as much as hours slept and no more than 24.")
            if time_to_fall_asleep < 0 or time_to_fall_asleep > 180:
                errors.append("Time to fall asleep must be between 0 and 180 minutes.")
            if not bed_time:
                errors.append("Bed time is required.")
            if not wake_time:
                errors.append("Wake time is required.")
            if errors:
                for err in errors:
                    st.error(err)
                st.stop()
            wakeup_count = 0
            if "3+" in wakeup_count_str:
                wakeup_count = 3
            elif "2" in wakeup_count_str:
                wakeup_count = 2
            elif "1" in wakeup_count_str:
                wakeup_count = 1
            
            woke_up_night = wakeup_count > 0

            # Ensure time inputs are not None before formatting
            bed_time_str = bed_time.strftime("%H:%M") if bed_time else "23:00"
            wake_time_str = wake_time.strftime("%H:%M") if wake_time else "07:00"
            
            # Calculate Sleep Efficiency
            bed_datetime = datetime.strptime(bed_time_str, "%H:%M")
            wake_datetime = datetime.strptime(wake_time_str, "%H:%M")
            if wake_datetime <= bed_datetime:
                wake_datetime += timedelta(days=1)
            time_in_bed_minutes = (wake_datetime - bed_datetime).total_seconds() / 60
            sleep_efficiency = (float(hours_slept) * 60 / time_in_bed_minutes) * 100 if time_in_bed_minutes > 0 else 0

            log = {
                "date": str(datetime.now().date()),
                "hours_slept": float(hours_slept),
                "time_in_bed": time_in_bed,
                "time_to_fall_asleep": time_to_fall_asleep,
                "bed_time": bed_time_str,
                "wake_time": wake_time_str,
                "sleep_efficiency": sleep_efficiency,
                "woke_up_feeling": woke_up_feeling,
                "woke_up_night": woke_up_night,
                "woke_up_times": wakeup_count,
                "quality_rating": quality_rating,
                "sleep_environment": sleep_environment,
                "mental_state": mental_state,
                "notes": notes
             }
            if save_user_log(st.session_state.user_uid, log):
                st.success("✅ Sleep logged successfully!")
                set_page("dashboard")
                time.sleep(0.2)  # Give Firestore a moment to write
                st.rerun()

