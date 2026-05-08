import os
import requests as http
from flask import Blueprint, redirect, request, current_app
from flask_login import login_user, logout_user
from urllib.parse import urlencode
from models import db, User

auth_bp = Blueprint("auth", __name__)


def _base():
    return os.environ.get("BASE_URL", "http://127.0.0.1:5000").rstrip("/")

def _cb(provider):
    return f"{_base()}/auth/{provider}/callback"


# ── Google ─────────────────────────────────────────────────────────────────────
@auth_bp.route("/auth/google")
def google_login():
    params = {
        "client_id":     os.environ.get("GOOGLE_CLIENT_ID", ""),
        "redirect_uri":  _cb("google"),
        "response_type": "code",
        "scope":         "openid email profile",
    }
    return redirect("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params))


@auth_bp.route("/auth/google/callback")
def google_callback():
    code = request.args.get("code")
    if not code:
        return redirect("/")
    try:
        token = http.post("https://oauth2.googleapis.com/token", data={
            "client_id":     os.environ.get("GOOGLE_CLIENT_ID"),
            "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET"),
            "code":          code,
            "redirect_uri":  _cb("google"),
            "grant_type":    "authorization_code",
        }, timeout=10).json()

        info = http.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {token['access_token']}"},
            timeout=10,
        ).json()

        user = User.query.filter_by(provider="google", provider_id=str(info["id"])).first()
        if not user:
            user = User(
                provider="google",
                provider_id=str(info["id"]),
                name=info.get("name", ""),
                email=info.get("email", ""),
                profile_image=info.get("picture", ""),
            )
            db.session.add(user)
            db.session.commit()

        login_user(user, remember=True)
    except Exception as e:
        current_app.logger.error(f"Google OAuth error: {e}")
    return redirect("/")


# ── Kakao ──────────────────────────────────────────────────────────────────────
@auth_bp.route("/auth/kakao")
def kakao_login():
    params = {
        "client_id":     os.environ.get("KAKAO_REST_API_KEY", ""),
        "redirect_uri":  _cb("kakao"),
        "response_type": "code",
    }
    return redirect("https://kauth.kakao.com/oauth/authorize?" + urlencode(params))


@auth_bp.route("/auth/kakao/callback")
def kakao_callback():
    code = request.args.get("code")
    if not code:
        return redirect("/")
    try:
        token = http.post(
            "https://kauth.kakao.com/oauth/token",
            data={
                "grant_type":   "authorization_code",
                "client_id":    os.environ.get("KAKAO_REST_API_KEY"),
                "redirect_uri": _cb("kakao"),
                "code":         code,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        ).json()

        info = http.get(
            "https://kapi.kakao.com/v2/user/me",
            headers={"Authorization": f"Bearer {token['access_token']}"},
            timeout=10,
        ).json()

        kakao_id = str(info["id"])
        account  = info.get("kakao_account", {})
        profile  = account.get("profile", {})

        user = User.query.filter_by(provider="kakao", provider_id=kakao_id).first()
        if not user:
            user = User(
                provider="kakao",
                provider_id=kakao_id,
                name=profile.get("nickname", ""),
                email=account.get("email", ""),
                profile_image=profile.get("profile_image_url", ""),
            )
            db.session.add(user)
            db.session.commit()

        login_user(user, remember=True)
    except Exception as e:
        current_app.logger.error(f"Kakao OAuth error: {e}")
    return redirect("/")


# ── Logout ─────────────────────────────────────────────────────────────────────
@auth_bp.route("/auth/logout")
def logout():
    logout_user()
    return redirect("/")
