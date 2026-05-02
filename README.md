# Facial Recognition Security System
### IoT · Deep Learning · Web App · Cloud Deployment

> Final year project (PFE) — Institut Supérieur de Gestion Industrielle de Sfax (ISGIS)
> Realized at **Laboratoires GALPHARMA**, Sfax, Tunisia — 2025/2026

---

## About the project

An intelligent IoT surveillance system for monitoring an emergency exit door
in a pharmaceutical warehouse.

When motion is detected, the ESP32-S3 camera captures a photo. If the door
opens within 30 seconds (confirmed by an inductive sensor), the image is sent
to a Flask server that runs facial recognition using Deep Learning (ResNet-34).
Results are stored in PostgreSQL and displayed in real-time on a web dashboard.

---

## System architecture

```
[PIR Sensor] ──► [ESP32-S3 T-SIM-CAM] ──► HTTP POST ──► [Flask Server]
[Inductive Sensor] ──────────────────┘                        │
[LCD I2C Display] ◄──── name/result ◄──────────────────────────┤
                                                               │
                                              ┌────────────────┼──────────────┐
                                         [recognition.py] [PostgreSQL]  [Cloudinary]
                                              │            (Supabase)   (photos CDN)
                                         [Web Dashboard] ◄─ polling 3s ─┘
```

---

## Tech stack

| Layer        | Technologies                                      |
|-------------|---------------------------------------------------|
| Hardware    | ESP32-S3 T-SIM-CAM, PIR sensor, inductive sensor, LCD I2C |
| Firmware    | C++ / Arduino / PlatformIO                        |
| Backend     | Python, Flask, face_recognition, OpenCV, dlib     |
| AI Model    | ResNet-34 (128-dim face embeddings), threshold 0.6|
| Database    | PostgreSQL (Supabase)                             |
| Storage     | Cloudinary (employee photos CDN)                  |
| Frontend    | HTML5, CSS3, JavaScript, Bootstrap 5, Jinja2      |
| Deployment  | Render (backend), GitHub (source), UptimeRobot    |

---

## Features

- Motion detection + door confirmation (anti false-positive logic)
- Real-time face recognition with ResNet-34 / dlib
- Live web dashboard with 3s polling
- Role-based access (Admin / Reader)
- Event history with Excel/PDF export
- Automatic email notification on new user creation
- Full cloud deployment (free tier)

---

## Project structure

```
facial-recognition-security-system/
├── esp32/
│   ├── src/main.cpp          # firmware logic
│   └── config.h              # WiFi, server URL
├── server/
│   ├── app.py                # Flask routes
│   ├── recognition.py        # face recognition pipeline
│   └── requirements.txt
├── web/
│   ├── templates/            # Jinja2 HTML templates
│   └── static/               # CSS, JS
├── database/                 # (gitignored) facial reference photos
├── docs/
│   └── screenshots/          # dashboard screenshots
└── README.md
```

---

## How to run locally

```bash
# 1. Clone the repo
git clone https://github.com/tasnim-yaich/facial-recognition-security-system
cd facial-recognition-security-system

# 2. Install Python dependencies
pip install -r server/requirements.txt

# 3. Set environment variables
export DATABASE_URL=your_supabase_url
export CLOUDINARY_CLOUD_NAME=your_cloud_name
export CLOUDINARY_API_KEY=your_key
export CLOUDINARY_API_SECRET=your_secret
export SECRET_KEY=your_flask_secret

# 4. Run the server
python server/app.py
```

---

## Live demo

Deployed on Render: https://controle-acces.onrender.com

---

## Author

**Tasnim Yaich** — Engineering student, ISGIS Sfax
tasnimyaich634@gmail.com · github.com/tasnim-yaich

---

## License

This project was developed as an academic final year project.
