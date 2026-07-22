from __future__ import annotations

SCHEMA_VERSION = 9
TABATLAS_EXTENSION_ID = "ohgpplkophdikjnbefigdhikdooehmkh"
TABATLAS_EXTENSION_URL_PREFIX = f"chrome-extension://{TABATLAS_EXTENSION_ID}/"
LEGACY_CAPTURE_PROTOCOL_VERSION = 2
TARGET_HASH_PROTOCOL_VERSION = 3
MUTATION_PROTOCOL_VERSION = 4
PUBLIC_PREVIEW_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36 TabAtlas/0.5"
)
TRACKING_PARAMETERS = {
    "fbclid",
    "gclid",
    "dclid",
    "msclkid",
    "mc_cid",
    "mc_eid",
    "igshid",
}
RESOURCE_STATUSES = {"open", "saved", "archived", "close_candidate"}
LIBRARY_STATES = {"accepted", "candidate", "dismissed"}
TASK_STATUSES = {"open", "done", "deferred", "cancelled"}
SPACE_DEFINITIONS = {
    "Produce Media & Stories": "AI video, animation, filmmaking, story craft, and visual production references.",
    "Make Games": "Game systems, real-time graphics, development techniques, and games worth studying.",
    "Build Software & Agents": "Coding agents, automation, architecture, product engineering, and interface work.",
    "Understand AI Models": "Model releases, capabilities, evaluation, training, inference, and research.",
    "Learn & Reference": "Durable history, architecture, science, engineering, and general knowledge.",
    "Personal & Admin": "Career, education, New Zealand life, shopping, travel, and personal operations.",
}
TOPIC_PARENT_DEFAULTS = {
    "AI Video & Animation": "Produce Media & Stories",
    "Film & Story Craft": "Produce Media & Stories",
    "Creative Tools": "Produce Media & Stories",
    "Music & Sound": "Produce Media & Stories",
    "Audio & Voice": "Produce Media & Stories",
    "Image Generation": "Produce Media & Stories",
    "Documentary & Events": "Produce Media & Stories",
    "Mods & Player Tools": "Make Games",
    "Real-Time Systems": "Make Games",
    "Game Design & Inspiration": "Make Games",
    "Artificial Life & Simulation": "Make Games",
    "Coding Agents": "Build Software & Agents",
    "Product & UI": "Build Software & Agents",
    "Software Engineering": "Build Software & Agents",
    "Models & Evaluation": "Understand AI Models",
    "Training & Inference": "Understand AI Models",
    "AI Safety": "Understand AI Models",
    "Architecture & Construction": "Learn & Reference",
    "Engineering & Industry": "Learn & Reference",
    "Travel": "Personal & Admin",
    "New Zealand Life": "Personal & Admin",
    "Accounts & Administration": "Personal & Admin",
    "Career": "Personal & Admin",
    "Shopping & Purchases": "Personal & Admin",
}
