Employee helper. Today we leverage Atlassian JIRA service management for employees to submit tickets when they need various forms of help (software access, hardware inssues, HR questions, production system issues, etc) and we have repetitive feedback that it's hard to find the right ticket to open and I was thinking about an AI solution that would be where all employees go to ask for help and then its trained on where the help is, and helps the employee navigate quickly to the right ticket form and or area to solve their need

We created a API Slackbot that utilizes NGROK API, connects to a Databricks backend that uses Llama 4 LLM to use Vector searching to parse through a Excel file we gave it that had all the contents of Best Egg's Portals.

Instructions:

Commands to get Ask-BestiE working: 

# Run the Chat Bot 
# Shell

cd C:\Projects\ask-bestiE 

# Can be skipped if it doesnt work
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process 

.\.venv\Scripts\Activate.ps1 

# install packages 
.\.venv\Scripts\python.exe -m pip install --upgrade pip 
.\.venv\Scripts\python.exe -m pip install -r requirements.txt 

# PyTorch install issues run: 
.\.venv\Scripts\python.exe -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu 
.\.venv\Scripts\python.exe -m pip install -r requirements.txt 

# Run the bot 
.\.venv\Scripts\python.exe -m app.slack_bot 

# You should see: 
* Running on http://127.0.0.1:3000 

# Start NGROK in a new window  
# Shell

cd C:\Projects\ask-bestiE 

# 1) Restart ngrok (new session) 
# In a new terminal (keep Flask running in the other tab): 
# optional: kill any lingering sessions 

Get-Process ngrok -ErrorAction SilentlyContinue | Stop-Process -Force 

# start a fresh tunnel to your local app 

& "C:\ngrok.exe" http http://127.0.0.1:3000 

# You should see a line like: 
# Forwarding  https://<something>.ngrok-free.app -> http://127.0.0.1:3000 
# Copy that https URL. 
# Copy the https forwarding URL and use it in Slack → Event Subscriptions → 
# Request URL with /slack/events appended. 
# IT SHOULD LOOK LIKE THIS: "https://b11fb6b07129.ngrok-free.app/slack/events" IF 
# IT IS MISSING "/slack/events" ADD IT. 
# If prompted, Install / Reinstall App (OAuth & Permissions). 
# Then in Slack: /invite @Ask-BestiE to your test channel. 
# 4) Test in Slack 
# Try: 
# @Ask-BestiE I'm locked out of LiveVox 
# You should get a short reply + Link + Required fields. 

# Third Terminal 
# Shell

curl http://127.0.0.1:3000/healthz 

# should return: ok 
# 1. In Slack → Event Subscriptions → Request URL, paste: 
# https://<your-ngrok>.ngrok-free.app/slack/events 
# 2. It should verify (green check). 
# 3. Under Subscribe to Bot Events, add: 
# ○ app_mention 
# ○ message.channels 
# ○ message.im 
# Save. 
# 4. OAuth & Permissions → (Re)Install App if prompted. 
# 5. In Slack: /invite @Ask-BestiE to your test channel. 
# 6. Test: @Ask-BestiE I'm locked out of LiveVox 