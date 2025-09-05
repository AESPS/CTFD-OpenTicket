# __init__.py - Complete timezone fix with UTC+8 support
import json
import time
from datetime import datetime, timezone, timedelta
from urllib import request as _rq, parse as _parse
from flask import Blueprint, request, jsonify, url_for, render_template, session

from CTFd.models import db, Users
from CTFd.utils.decorators import authed_only, admins_only
from CTFd.plugins import register_plugin_assets_directory
from CTFd.utils.user import get_current_user

from .models import SupportTicket, SupportMessage, UserNotification

bp = Blueprint("support_chat", __name__, template_folder="templates")

# Timezone configuration - UTC+8 (Singapore/Malaysia time)
DISPLAY_TIMEZONE = timezone(timedelta(hours=8))

def format_datetime_for_display(dt):
    """Convert UTC datetime to UTC+8 for display"""
    if not dt:
        return None
    
    # If dt is naive, assume it's UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    
    # Convert to UTC+8
    local_dt = dt.astimezone(DISPLAY_TIMEZONE)
    return local_dt

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

def _get_or_create_open_ticket(user_id: int):
    """Get existing open ticket or create a new one"""
    # Try to get existing open ticket first
    ticket = SupportTicket.query.filter_by(user_id=user_id, status="open").first()
    
    if ticket:
        return ticket, False  # existing ticket, not created
    
    # Create new ticket
    ticket = SupportTicket(user_id=user_id, status="open")
    db.session.add(ticket)
    db.session.flush()  # Get the ID immediately
    return ticket, True  # new ticket, created

# -------------------- USER --------------------
@bp.route("/support/ticket", methods=["GET"])
@authed_only
def get_or_create_ticket():
    """Get ticket info - only creates ticket when user sends first message"""
    u = get_current_user()
    t = _get_open_ticket_for_user(u.id)
    
    if not t:
        # Don't create ticket yet - just return empty state
        return jsonify({
            "ticket_id": None,
            "status": None,
            "messages": [],
            "unread_admin_count": 0
        })
    
    # User has existing ticket - show messages and notifications
    msgs = (SupportMessage.query.filter_by(ticket_id=t.id)
            .order_by(SupportMessage.created.asc()).all())
    
    # Get or create notification record
    notification = UserNotification.query.filter_by(
        user_id=u.id, 
        ticket_id=t.id
    ).first()
    
    if not notification:
        notification = UserNotification(
            user_id=u.id,
            ticket_id=t.id,
            last_seen_message_id=0,
            unread_admin_count=0
        )
        db.session.add(notification)
        db.session.commit()
    
    # Calculate unread admin messages
    unread_admin_messages = 0
    if msgs:
        last_seen_id = notification.last_seen_message_id
        unread_admin_messages = SupportMessage.query.filter(
            SupportMessage.ticket_id == t.id,
            SupportMessage.sender_role == "admin",
            SupportMessage.id > last_seen_id
        ).count()
        
        # Update the notification record with current count
        notification.unread_admin_count = unread_admin_messages
        db.session.commit()
    
    return jsonify({
        "ticket_id": t.id,
        "status": t.status,
        "messages": [m.to_dict() for m in msgs],
        "unread_admin_count": unread_admin_messages
    })

@bp.route("/support/message", methods=["POST"])
@authed_only
def post_user_message():
    """Create ticket when user sends first message"""
    u = get_current_user()
    text = (request.values.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "Empty message"}), 400

    # Get existing ticket OR create new one when user sends first message
    t = _get_open_ticket_for_user(u.id)
    if not t:
        # Create ticket only when user actually sends a message
        t = _create_open_ticket(u.id)
        print(f"[TICKET] Created new ticket #{t.id} for user {u.name} ({u.id})")

    m = SupportMessage(ticket_id=t.id, sender_role="user", sender_id=u.id, text=text)
    t.updated = datetime.utcnow()
    db.session.add(m)
    db.session.flush()  # Get the message ID
    
    # Update user's last seen message (user sees their own message immediately)
    notification = UserNotification.query.filter_by(
        user_id=u.id, 
        ticket_id=t.id
    ).first()
    
    if notification:
        notification.last_seen_message_id = m.id
        notification.updated = datetime.utcnow()
    else:
        notification = UserNotification(
            user_id=u.id,
            ticket_id=t.id,
            last_seen_message_id=m.id,
            unread_admin_count=0
        )
        db.session.add(notification)
    
    db.session.commit()
    return jsonify({"ok": True, "message": m.to_dict()})

@bp.route("/support/mark_read", methods=["POST"])
@authed_only
def mark_messages_read():
    """Mark messages as read - only if ticket exists"""
    u = get_current_user()
    nonce = request.values.get("nonce", "")
    
    # CSRF validation
    if nonce != session.get("nonce", ""):
        return jsonify({"ok": False, "error": "Invalid nonce"}), 403
    
    t = _get_open_ticket_for_user(u.id)
    if not t:
        # No ticket exists - nothing to mark as read
        return jsonify({"ok": True, "unread_count": 0})
    
    # Get the latest message ID
    latest_message = SupportMessage.query.filter_by(ticket_id=t.id).order_by(
        SupportMessage.id.desc()
    ).first()
    
    if not latest_message:
        return jsonify({"ok": True, "unread_count": 0})
    
    # Update notification record
    notification = UserNotification.query.filter_by(
        user_id=u.id, 
        ticket_id=t.id
    ).first()
    
    if notification:
        notification.last_seen_message_id = latest_message.id
        notification.unread_admin_count = 0
        notification.updated = datetime.utcnow()
    else:
        notification = UserNotification(
            user_id=u.id,
            ticket_id=t.id,
            last_seen_message_id=latest_message.id,
            unread_admin_count=0
        )
        db.session.add(notification)
    
    db.session.commit()
    
    return jsonify({"ok": True, "unread_count": 0})

@bp.route("/support/unread_count", methods=["GET"])
@authed_only
def get_unread_count():
    """Get unread count - don't create ticket if none exists"""
    u = get_current_user()
    t = _get_open_ticket_for_user(u.id)
    
    if not t:
        # No ticket exists - no unread messages
        return jsonify({"unread_count": 0})
    
    notification = UserNotification.query.filter_by(
        user_id=u.id, 
        ticket_id=t.id
    ).first()
    
    if not notification:
        # Count all admin messages as unread
        unread_count = SupportMessage.query.filter(
            SupportMessage.ticket_id == t.id,
            SupportMessage.sender_role == "admin"
        ).count()
        
        # Create notification record only if there are messages
        if unread_count > 0:
            notification = UserNotification(
                user_id=u.id,
                ticket_id=t.id,
                last_seen_message_id=0,
                unread_admin_count=unread_count
            )
            db.session.add(notification)
            db.session.commit()
    else:
        # Count admin messages since last seen
        unread_count = SupportMessage.query.filter(
            SupportMessage.ticket_id == t.id,
            SupportMessage.sender_role == "admin",
            SupportMessage.id > notification.last_seen_message_id
        ).count()
        
        # Update the count in database
        notification.unread_admin_count = unread_count
        db.session.commit()
    
    return jsonify({"unread_count": unread_count})

# -------------------- ADMIN --------------------
@bp.route("/support/admin", methods=["GET"])
@admins_only
def support_admin_home():
    # Get tickets with better user lookup
    tickets = SupportTicket.query.order_by(SupportTicket.updated.desc()).all()
    
    # Add user and team info to each ticket with better error handling
    for ticket in tickets:
        if ticket.user_id:
            try:
                user = Users.query.get(ticket.user_id)
                if user:
                    # Try to get team info
                    try:
                        from CTFd.models import Teams
                        if hasattr(user, 'team_id') and user.team_id:
                            team = Teams.query.get(user.team_id)
                            user.team = team
                        else:
                            user.team = None
                    except Exception as e:
                        print(f"[DEBUG] Team lookup failed: {e}")
                        user.team = None
                    
                    ticket.user = user
                else:
                    print(f"[WARNING] User {ticket.user_id} not found for ticket {ticket.id}")
                    ticket.user = None
            except Exception as e:
                print(f"[ERROR] User lookup failed for ticket {ticket.id}: {e}")
                ticket.user = None
        else:
            ticket.user = None
        
        # Calculate unread user messages for this ticket (for admin)
        if ticket.user_id:
            notification = UserNotification.query.filter_by(
                user_id=ticket.user_id, 
                ticket_id=ticket.id
            ).first()
            
            if notification:
                # Messages from user that came after the user's last seen message
                # This represents messages the admin might not have seen yet
                ticket.unread_user_messages = SupportMessage.query.filter(
                    SupportMessage.ticket_id == ticket.id,
                    SupportMessage.sender_role == "user",
                    SupportMessage.id > notification.last_seen_message_id
                ).count()
            else:
                # If no notification record, count all recent user messages as potentially unread
                ticket.unread_user_messages = SupportMessage.query.filter(
                    SupportMessage.ticket_id == ticket.id,
                    SupportMessage.sender_role == "user"
                ).count()
        else:
            ticket.unread_user_messages = 0
    
    # Convert ticket timestamps to UTC+8 for consistent display
    for ticket in tickets:
        if ticket.updated:
            ticket.updated = format_datetime_for_display(ticket.updated)
        if ticket.created:
            ticket.created = format_datetime_for_display(ticket.created)
    
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
        # Get team information
        team = None
        team_name = None
        if hasattr(user, 'team_id') and user.team_id:
            try:
                from CTFd.models import Teams
                team = Teams.query.get(user.team_id)
                team_name = team.name if team else None
            except:
                pass
        
        user_data = {
            "id": user.id, 
            "name": user.name, 
            "email": user.email,
            "team_name": team_name
        }
    
    # Also get team info for message senders
    messages_with_team = []
    for m in msgs:
        msg_dict = m.to_dict()
        if m.sender_id:
            sender = Users.query.get(m.sender_id)
            if sender and hasattr(sender, 'team_id') and sender.team_id:
                try:
                    from CTFd.models import Teams
                    sender_team = Teams.query.get(sender.team_id)
                    msg_dict['sender_name'] = sender.name
                    msg_dict['sender_team'] = sender_team.name if sender_team else None
                except:
                    msg_dict['sender_name'] = sender.name
                    msg_dict['sender_team'] = None
            elif sender:
                msg_dict['sender_name'] = sender.name
                msg_dict['sender_team'] = None
        messages_with_team.append(msg_dict)
    
    # Format timestamps for UTC+8 display
    created_display = format_datetime_for_display(t.created)
    updated_display = format_datetime_for_display(t.updated)
    
    return jsonify({
        "ticket": {
            "id": t.id,
            "user_id": t.user_id,
            "user": user_data,
            "status": t.status,
            "created": created_display.isoformat() if created_display else None,
            "updated": updated_display.isoformat() if updated_display else None,
            "messages": messages_with_team
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
    
    # Create admin message
    m = SupportMessage(ticket_id=ticket_id, sender_role="admin", sender_id=admin.id, text=text)
    t.updated = datetime.utcnow()
    db.session.add(m)
    db.session.flush()  # Get the message ID
    
    # Update user notification count
    notification = UserNotification.query.filter_by(
        user_id=t.user_id, 
        ticket_id=ticket_id
    ).first()
    
    if notification:
        # Increment unread admin message count
        notification.unread_admin_count += 1
        notification.updated = datetime.utcnow()
    else:
        # Create new notification record
        notification = UserNotification(
            user_id=t.user_id,
            ticket_id=ticket_id,
            last_seen_message_id=0,
            unread_admin_count=1
        )
        db.session.add(notification)
    
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

@bp.route("/support/admin/delete", methods=["POST"])
@admins_only
def support_admin_delete():
    ticket_id = request.values.get("ticket_id")
    if not ticket_id:
        return jsonify({"ok": False, "error": "Missing ticket_id"}), 400
    
    try:
        ticket_id = int(ticket_id)
    except ValueError:
        return jsonify({"ok": False, "error": "Invalid ticket_id"}), 400
    
    t = SupportTicket.query.get_or_404(ticket_id)
    
    # Delete notification records first
    UserNotification.query.filter_by(ticket_id=ticket_id).delete()
    
    # Delete all messages first (foreign key constraint)
    SupportMessage.query.filter_by(ticket_id=ticket_id).delete()
    
    # Then delete the ticket
    db.session.delete(t)
    db.session.commit()
    
    return jsonify({"ok": True, "message": "Ticket deleted successfully"})

# -------------------- BROADCAST --------------------
@bp.route("/support/admin/broadcast", methods=["GET", "POST"])
@admins_only
def support_admin_broadcast():
    if request.method == "GET":
        return render_template("support_broadcast.html")
    
    # Handle POST - send broadcast
    message = (request.values.get("message") or "").strip()
    target = request.values.get("target", "all")
    team_id = request.values.get("team_id")
    nonce = request.values.get("nonce", "")
    
    # CSRF validation
    if nonce != session.get("nonce", ""):
        return jsonify({"ok": False, "error": "Invalid nonce"}), 403
    
    if not message:
        return jsonify({"ok": False, "error": "Empty message"}), 400
    
    admin = get_current_user()
    current_time = datetime.utcnow()
    
    try:
        if target == "all":
            return _broadcast_to_all_users(message, admin, current_time)
        elif target == "open_tickets":
            return _broadcast_to_open_tickets(message, admin, current_time)
        elif target == "specific_team" and team_id:
            return _broadcast_to_team(message, admin, current_time, team_id)
        else:
            return jsonify({"ok": False, "error": "Invalid target"}), 400
            
    except Exception as e:
        print(f"[BROADCAST ERROR] {str(e)}")
        db.session.rollback()  # Ensure rollback on error
        return jsonify({"ok": False, "error": f"Server error: {str(e)}"}), 500

def _broadcast_to_all_users(message, admin, current_time):
    """Broadcast to all users with improved error handling and batch processing"""
    
    # Get all users in batches to avoid memory issues
    BATCH_SIZE = 100
    total_users = Users.query.count()
    tickets_created = 0
    messages_sent = 0
    errors = []
    
    print(f"[BROADCAST] Starting broadcast to {total_users} users in batches of {BATCH_SIZE}")
    
    # Process users in batches
    for offset in range(0, total_users, BATCH_SIZE):
        users_batch = Users.query.offset(offset).limit(BATCH_SIZE).all()
        
        try:
            # Process each batch in a separate transaction
            batch_created, batch_sent = _process_user_batch(users_batch, message, admin, current_time)
            tickets_created += batch_created
            messages_sent += batch_sent
            
            print(f"[BROADCAST] Processed batch {offset//BATCH_SIZE + 1}: {batch_sent} messages sent")
            
        except Exception as e:
            error_msg = f"Batch {offset//BATCH_SIZE + 1} failed: {str(e)}"
            errors.append(error_msg)
            print(f"[BROADCAST ERROR] {error_msg}")
            db.session.rollback()
            continue  # Continue with next batch
    
    # Return results
    if errors:
        error_summary = f" ({len(errors)} batches had errors)" if messages_sent > 0 else ""
        return jsonify({
            "ok": True,
            "message": f"Broadcast sent to {messages_sent} users ({tickets_created} new tickets){error_summary}",
            "errors": errors
        })
    else:
        return jsonify({
            "ok": True, 
            "message": f"Broadcast sent to {messages_sent} users ({tickets_created} new tickets created)"
        })

def _process_user_batch(users_batch, message, admin, current_time):
    """Process a batch of users for broadcasting"""
    tickets_created = 0
    messages_sent = 0
    
    for user in users_batch:
        try:
            # Check if user already has an open ticket
            existing_ticket = SupportTicket.query.filter_by(
                user_id=user.id, 
                status="open"
            ).first()
            
            if existing_ticket:
                # Use existing ticket
                ticket = existing_ticket
            else:
                # Create new ticket
                ticket = SupportTicket(user_id=user.id, status="open")
                db.session.add(ticket)
                tickets_created += 1
            
            # Add broadcast message
            broadcast_message = SupportMessage(
                ticket_id=ticket.id if existing_ticket else None,  # Will be set after flush for new tickets
                sender_role="admin", 
                sender_id=admin.id, 
                text=f"[BROADCAST] {message}"
            )
            
            # Update ticket timestamp
            ticket.updated = current_time
            
            # If it's a new ticket, we need to flush to get the ID
            if not existing_ticket:
                db.session.flush()  # Get the ticket ID without committing
                broadcast_message.ticket_id = ticket.id
            
            db.session.add(broadcast_message)
            db.session.flush()  # Get message ID
            messages_sent += 1
            
            # Update notification count for broadcast messages
            notification = UserNotification.query.filter_by(
                user_id=user.id,
                ticket_id=ticket.id
            ).first()
            
            if notification:
                notification.unread_admin_count += 1
                notification.updated = current_time
            else:
                notification = UserNotification(
                    user_id=user.id,
                    ticket_id=ticket.id,
                    last_seen_message_id=0,
                    unread_admin_count=1
                )
                db.session.add(notification)
            
        except Exception as e:
            print(f"[BROADCAST] Failed to process user {user.id} ({user.name}): {str(e)}")
            # Continue with other users in the batch
            continue
    
    # Commit the entire batch
    db.session.commit()
    return tickets_created, messages_sent

def _broadcast_to_open_tickets(message, admin, current_time):
    """Broadcast to users with open tickets"""
    try:
        open_tickets = SupportTicket.query.filter_by(status="open").all()
        messages_sent = 0
        
        for ticket in open_tickets:
            try:
                broadcast_message = SupportMessage(
                    ticket_id=ticket.id,
                    sender_role="admin",
                    sender_id=admin.id,
                    text=f"[BROADCAST] {message}"
                )
                ticket.updated = current_time
                db.session.add(broadcast_message)
                db.session.flush()  # Get message ID
                
                # Update notification count
                notification = UserNotification.query.filter_by(
                    user_id=ticket.user_id,
                    ticket_id=ticket.id
                ).first()
                
                if notification:
                    notification.unread_admin_count += 1
                    notification.updated = current_time
                else:
                    notification = UserNotification(
                        user_id=ticket.user_id,
                        ticket_id=ticket.id,
                        last_seen_message_id=0,
                        unread_admin_count=1
                    )
                    db.session.add(notification)
                
                messages_sent += 1
            except Exception as e:
                print(f"[BROADCAST] Failed to send to ticket {ticket.id}: {str(e)}")
                continue
        
        db.session.commit()
        return jsonify({
            "ok": True,
            "message": f"Broadcast sent to {messages_sent} open tickets"
        })
        
    except Exception as e:
        db.session.rollback()
        raise e

def _broadcast_to_team(message, admin, current_time, team_id):
    """Broadcast to users in a specific team"""
    try:
        from CTFd.models import Teams
        team = Teams.query.get_or_404(team_id)
        team_users = Users.query.filter_by(team_id=team_id).all()
        
        tickets_created = 0
        messages_sent = 0
        
        for user in team_users:
            try:
                # Get or create open ticket
                ticket = SupportTicket.query.filter_by(
                    user_id=user.id, 
                    status="open"
                ).first()
                
                if not ticket:
                    ticket = SupportTicket(user_id=user.id, status="open")
                    db.session.add(ticket)
                    db.session.flush()  # Get ID
                    tickets_created += 1
                
                broadcast_message = SupportMessage(
                    ticket_id=ticket.id,
                    sender_role="admin", 
                    sender_id=admin.id,
                    text=f"[BROADCAST to {team.name}] {message}"
                )
                ticket.updated = current_time
                db.session.add(broadcast_message)
                db.session.flush()
                
                # Update notification count
                notification = UserNotification.query.filter_by(
                    user_id=user.id,
                    ticket_id=ticket.id
                ).first()
                
                if notification:
                    notification.unread_admin_count += 1
                    notification.updated = current_time
                else:
                    notification = UserNotification(
                        user_id=user.id,
                        ticket_id=ticket.id,
                        last_seen_message_id=0,
                        unread_admin_count=1
                    )
                    db.session.add(notification)
                
                messages_sent += 1
                
            except Exception as e:
                print(f"[BROADCAST] Failed to process team user {user.id}: {str(e)}")
                continue
        
        db.session.commit()
        return jsonify({
            "ok": True,
            "message": f"Broadcast sent to {messages_sent} users in team {team.name} ({tickets_created} new tickets)"
        })
        
    except Exception as e:
        db.session.rollback()
        raise e

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
def _detect_lang(text):
    """Simple language detection based on common patterns"""
    if not text:
        return 'en'
        
    text_lower = text.lower()
    
    # Check for common Malay/Indonesian words
    malay_words = ['saya', 'anda', 'dengan', 'untuk', 'dari', 'ini', 'itu', 'yang', 'ada', 'tidak', 'dia', 'terima kasih', 'selamat', 'tolong', 'masalah', 'bagaimana']
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
    vietnamese_words = ['tôi', 'bạn', 'với', 'để', 'từ', 'không', 'mà', 'có', 'anh', 'chị', 'xin chào', 'cảm ơn', 'giúp', 'vấn đề']
    if any(char in text_lower for char in vietnamese_chars) or any(word in text_lower for word in vietnamese_words):
        return 'vi'
    
    # Default to English
    return 'en'

def _try_external_translation(text, source_lang, target_lang):
    """Try external translation services"""
    try:
        import urllib.parse
        import urllib.request
        import json
        
        # Try MyMemory API (free, no API key needed)
        base_url = "https://api.mymemory.translated.net/get"
        params = urllib.parse.urlencode({
            'q': text[:200],  # Limit length
            'langpair': f'{source_lang}|{target_lang}'
        })
        
        url = f"{base_url}?{params}"
        req = urllib.request.Request(url, headers={'User-Agent': 'CTFd Support Chat'})
        
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            
            if 'responseData' in data and data['responseData']:
                api_translation = data['responseData'].get('translatedText', '')
                if api_translation and api_translation.lower().strip() != text.lower().strip():
                    return api_translation
                    
    except Exception as e:
        print(f"[TRANSLATE] External API failed: {e}")
    
    return None

def _simple_translate_dict(text, source_lang):
    """Very basic fallback translation - only for common single words/phrases"""
    
    # Keep this very simple - only translate obvious single words or fixed phrases
    translations = {
        'ms': {  # Only very common single words/phrases
            'saya': 'I',
            'anda': 'you', 
            'tidak': 'no',
            'ya': 'yes',
            'terima kasih': 'thank you',
            'maaf': 'sorry',
            'tolong': 'help',
            'bantuan': 'help',
        },
        'vi': {
            'tôi': 'I',
            'bạn': 'you',
            'không': 'no',
            'có': 'yes',
            'xin chào': 'hello',
            'cảm ơn': 'thank you',
        }
    }
    
    if source_lang not in translations:
        return text
    
    # Only translate if it's a single phrase or very short
    text_lower = text.lower().strip()
    if text_lower in translations[source_lang]:
        return translations[source_lang][text_lower]
    
    # Don't attempt word-by-word for sentences
    return text

@bp.route("/support/translate", methods=["POST"])
@authed_only
def translate_text():
    text = (request.values.get("text") or "").strip()
    target = (request.values.get("target") or "en").strip().lower() or "en"
    nonce = request.values.get("nonce", "")
    
    # CSRF validation
    if nonce != session.get("nonce", ""):
        return jsonify({"ok": False, "error": "Invalid nonce"}), 403
    
    if not text:
        return jsonify({"ok": False, "error": "Empty text"}), 400

    try:
        # Detect source language
        source = _detect_lang(text)
        
        # If source is already target language, return original
        if source == target or source == 'en':
            return jsonify({
                "ok": True, 
                "translated": text, 
                "target": target, 
                "source": source,
                "note": "Already in English or target language"
            })

        # Try external translation service FIRST (it handles grammar properly)
        external_translation = _try_external_translation(text, source, target)
        if external_translation:
            return jsonify({
                "ok": True,
                "translated": external_translation,
                "target": target,
                "source": source,
                "changed": True,
                "method": "external_api"
            })
        
        # Only fall back to simple dictionary if external API fails
        simple_translation = _simple_translate_dict(text, source)
        if simple_translation.lower() != text.lower():
            return jsonify({
                "ok": True,
                "translated": simple_translation + " (basic translation)",
                "target": target,
                "source": source,
                "changed": True,
                "method": "dictionary_fallback"
            })
        
        # If nothing worked, return original
        return jsonify({
            "ok": True,
            "translated": text,
            "target": target,
            "source": source,
            "changed": False,
            "note": "Translation not available for this text"
        })
        
    except Exception as e:
        print(f"[TRANSLATE] Error: {e}")
        return jsonify({
            "ok": True,  # Don't fail, just return original
            "translated": text,
            "target": target,
            "source": "unknown",
            "note": f"Translation failed: {str(e)}"
        })

# -------------------- LOAD & ASSETS --------------------
def load(app):
    with app.app_context():
        # Create all tables including the new UserNotification table
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