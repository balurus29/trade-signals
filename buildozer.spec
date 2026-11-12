[app]
title = Trade Signals
package.name = tradesignals
package.domain = org.example
source.dir = .
source.include_exts = py
version = 0.3
requirements = python3,kivy==2.3.0,numpy,certifi,plyer
orientation = portrait
fullscreen = 0
android.permissions = INTERNET,VIBRATE,POST_NOTIFICATIONS
android.api = 33
android.minapi = 24
android.ndk = 25b
android.ndk_api = 24
android.archs = arm64-v8a
android.accept_sdk_license = True
# проверенная связка: Python 3.11 + Kivy 2.3.0 (новая ветка p4a тянет Python 3.14, с которым сборка падает)
p4a.branch = v2024.01.21

[buildozer]
log_level = 2
warn_on_root = 1
