import json
import time
from datetime import datetime
from urllib import request as _rq, parse as _parse
from flask import Blueprint, request, jsonify, url_for, render_template, session

from CTFd.models import db, Users
from CTFd.utils.decorators import authed_only, admins_only
from CTFd.plugins import register_plugin_assets_directory
from CTFd.utils.user import get_current_user

from .models import SupportTicket, SupportMessage

bp = Blueprint("support_chat", __name__, template_folder="templates")

_last_translate = 0.0
def _throttle(min_interval=0.75):
    global _last_translate
    now = time.time()
    if now - _last_translate < min_interval:
        time.sleep(min_interval - (now - _last_translate))
    _last_translate = time.time()

@bp.route("/support/nonce", methods=["GET"])
@authed_only
def support_nonce():
    return jsonify({"nonce": session.get("nonce", "")})

# ---------- helpers ----------
def _get_open_ticket_for_user(user_id: int):
    return SupportTicket.query.filter_by(user_id=user_id, status="open").order_by(SupportTicket.id.desc()).first()

def _create_open_ticket(user_id: int):
    t = SupportTicket(user_id=user_id, status="open")
    db.session.add(t)
    db.session.commit()
    return t

# -------------------- USER --------------------
@bp.route("/support/ticket", methods=["GET"])
@authed_only
def get_or_create_ticket():
    u = get_current_user()
    t = _get_open_ticket_for_user(u.id)
    if not t:
        t = _create_open_ticket(u.id)
    msgs = (SupportMessage.query.filter_by(ticket_id=t.id)
            .order_by(SupportMessage.created.asc()).all())
    return jsonify({
        "ticket_id": t.id,
        "status": t.status,
        "messages": [m.to_dict() for m in msgs]
    })

@bp.route("/support/message", methods=["POST"])
@authed_only
def post_user_message():
    u = get_current_user()
    text = (request.values.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "Empty message"}), 400

    # Always post into an OPEN ticket; create a new one if last is closed
    t = _get_open_ticket_for_user(u.id)
    if not t:
        t = _create_open_ticket(u.id)

    m = SupportMessage(ticket_id=t.id, sender_role="user", sender_id=u.id, text=text)
    t.updated = datetime.utcnow()
    db.session.add(m)
    db.session.commit()
    return jsonify({"ok": True, "message": m.to_dict()})

# -------------------- ADMIN --------------------
@bp.route("/support/admin", methods=["GET"])
@admins_only
def support_admin_home():
    # Get tickets with user and team information
    tickets = SupportTicket.query.order_by(SupportTicket.updated.desc()).all()
    
    # Add team information to each ticket
    for ticket in tickets:
        if ticket.user_id:
            user = Users.query.get(ticket.user_id)
            if user and hasattr(user, 'team_id') and user.team_id:
                try:
                    from CTFd.models import Teams
                    team = Teams.query.get(user.team_id)
                    user.team = team  # Add team object to user
                except:
                    user.team = None
            elif user:
                user.team = None
    
    return render_template("support_admin.html", tickets=tickets)

@bp.route("/support/admin/ticket/<int:ticket_id>", methods=["GET"])
@admins_only
def support_admin_ticket(ticket_id):
    t = SupportTicket.query.get_or_404(ticket_id)
    msgs = (SupportMessage.query.filter_by(ticket_id=ticket_id)
            .order_by(SupportMessage.created.asc()).all())
    user = Users.query.get(t.user_id) if t.user_id else None
    user_data = None
    if user:
        user_data = {"id": user.id, "name": user.name, "email": user.email}
    
    return jsonify({
        "ticket": {
            "id": t.id,
            "user_id": t.user_id,
            "user": user_data,
            "status": t.status,
            "created": t.created.isoformat() if t.created else None,
            "updated": t.updated.isoformat() if t.updated else None,
            "messages": [m.to_dict() for m in msgs]
        }
    })

@bp.route("/support/admin/reply", methods=["POST"])
@admins_only
def support_admin_reply():
    ticket_id = request.values.get("ticket_id")
    if not ticket_id:
        return jsonify({"ok": False, "error": "Missing ticket_id"}), 400
    
    try:
        ticket_id = int(ticket_id)
    except ValueError:
        return jsonify({"ok": False, "error": "Invalid ticket_id"}), 400
    
    t = SupportTicket.query.get_or_404(ticket_id)
    if t.status != "open":
        return jsonify({"ok": False, "error": "Ticket is closed"}), 400
    
    admin = get_current_user()
    text = (request.values.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "Empty message"}), 400
    
    m = SupportMessage(ticket_id=ticket_id, sender_role="admin", sender_id=admin.id, text=text)
    t.updated = datetime.utcnow()
    db.session.add(m)
    db.session.commit()
    return jsonify({"ok": True, "message": m.to_dict()})

@bp.route("/support/admin/close", methods=["POST"])
@admins_only
def support_admin_close():
    ticket_id = request.values.get("ticket_id")
    if not ticket_id:
        return jsonify({"ok": False, "error": "Missing ticket_id"}), 400
    
    try:
        ticket_id = int(ticket_id)
    except ValueError:
        return jsonify({"ok": False, "error": "Invalid ticket_id"}), 400
    
    t = SupportTicket.query.get_or_404(ticket_id)
    t.status = "closed"
    t.updated = datetime.utcnow()
    db.session.commit()
    return jsonify({"ok": True, "status": "closed"})

# Keep the old status endpoint for backward compatibility
@bp.route("/support/admin/status/<int:tid>", methods=["POST"])
@admins_only
def support_admin_status(tid):
    t = SupportTicket.query.get_or_404(tid)
    status = (request.values.get("status") or "").strip().lower()
    if status not in ("open", "closed", "pending"):
        return jsonify({"ok": False, "error": "Bad status"}), 400
    t.status = status
    t.updated = datetime.utcnow()
    db.session.commit()
    return jsonify({"ok": True, "status": status})

# -------------------- TRANSLATION --------------------
def _http_json(url, data=None, headers=None, timeout=8):
    req = _rq.Request(url, headers=headers or {})
    if data is not None:
        if isinstance(data, dict):
            payload = json.dumps(data).encode("utf-8")
            req.add_header("Content-Type", "application/json")
            req.data = payload
        else:
            req.data = data
    with _rq.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        try:
            return json.loads(raw.decode("utf-8", "ignore"))
        except Exception:
            return {}

def _detect_lang(text):
    """Simple language detection based on common patterns"""
    # Check for common Malay/Indonesian words
    malay_words = ['saya', 'anda', 'dengan', 'untuk', 'dari', 'ini', 'itu', 'yang', 'ada', 'tidak', 'dia']
    text_lower = text.lower()
    if any(word in text_lower for word in malay_words):
        return 'ms'
    
    # Check for Thai characters
    if any('\u0e00' <= char <= '\u0e7f' for char in text):
        return 'th'
    
    # Check for Khmer characters  
    if any('\u1780' <= char <= '\u17ff' for char in text):
        return 'km'
    
    # Check for Vietnamese characters
    vietnamese_chars = ['ă', 'â', 'đ', 'ê', 'ô', 'ơ', 'ư', 'à', 'á', 'ả', 'ã', 'ạ']
    if any(char in text_lower for char in vietnamese_chars):
        return 'vi'
    
    # Default to English if no patterns match
    return 'en'

def _translate_mymemory(text, source_lang, target_lang):
    """Use MyMemory API as primary translation service"""
    try:
        base = "https://api.mymemory.translated.net/get"
        params = {
            "q": text[:500],  # Limit text length
            "langpair": f"{source_lang}|{target_lang}"
        }
        qs = _parse.urlencode(params)
        data = _http_json(f"{base}?{qs}", timeout=6)
        
        if isinstance(data, dict) and "responseData" in data:
            resp = data["responseData"]
            translated = resp.get("translatedText", "")
            if translated and translated.lower() != text.lower():
                return translated
    except Exception as e:
        print(f"MyMemory translation error: {e}")
    return None

def _translate_best_effort(text, target):
    """Improved translation with better error handling"""
    if not text or not text.strip():
        return text
        
    # Detect source language
    source = _detect_lang(text)
    
    # If already in target language, return original
    if source == target:
        return text
    
    # Try MyMemory first (more reliable than LibreTranslate)
    translated = _translate_mymemory(text, source, target)
    if translated:
        return translated
    
    # Try LibreTranslate as fallback
    try:
        lt_url = "https://libretranslate.com/translate"
        payload = {
            "q": text[:300],  # Limit text length
            "source": source,
            "target": target,
            "format": "text"
        }
        
        data = _http_json(lt_url, data=json.dumps(payload).encode("utf-8"),
                          headers={"Content-Type": "application/json"}, timeout=6)
        
        if isinstance(data, dict):
            translated = data.get("translatedText") or data.get("translated_text")
            if translated and translated.strip() and translated.lower() != text.lower():
                return translated
    except Exception as e:
        print(f"LibreTranslate error: {e}")
    
    # If all else fails, return original text
    return text

@bp.route("/support/translate", methods=["POST"])
@authed_only
def translate_text():
    text = (request.values.get("text") or "").strip()
    target = (request.values.get("target") or "en").strip().lower() or "en"
    
    if not text:
        return jsonify({"ok": False, "error": "Empty text"}), 400

    # Detect source language
    source = _detect_lang(text)
    
    # If source is already target language, return original
    if source == target:
        return jsonify({
            "ok": True, 
            "translated": text, 
            "target": target, 
            "source": source,
            "note": "Already in target language"
        })
    
    # Only proceed if source is not English (or if target is not English)
    if source == 'en' and target == 'en':
        return jsonify({
            "ok": True,
            "translated": text,
            "target": target,
            "source": source,
            "note": "Text appears to be in English"
        })

    try:
        translated = _translate_best_effort(text, target)
        
        return jsonify({
            "ok": True,
            "translated": translated,
            "target": target,
            "source": source,
            "changed": translated != text
        })
        
    except Exception as e:
        print(f"Translation error: {e}")
        return jsonify({
            "ok": False,
            "error": "Translation service unavailable",
            "original": text
        }), 500

# -------------------- LOAD & ASSETS --------------------
def load(app):
    with app.app_context():
        db.create_all()

    register_plugin_assets_directory(
        app, base_path="/plugins/support_chat/assets", endpoint="support_chat_assets"
    )
    app.register_blueprint(bp)

    try:
        from CTFd.plugins import register_admin_plugin_menu_bar
        register_admin_plugin_menu_bar(title="Support Chat", route="/support/admin")
    except Exception:
        pass

    @app.context_processor
    def inject_support_widget():
        def support_chat_assets():
            css = url_for("support_chat_assets", path="support.css")
            js = url_for("support_chat_assets", path="support.js")
            return f'<link rel="stylesheet" href="{css}"><script src="{js}" defer></script>'
        return dict(support_chat_assets=support_chat_assets)