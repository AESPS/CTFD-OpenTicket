# models.py - Updated with timezone-aware to_dict() method

from datetime import datetime, timezone, timedelta
from CTFd.models import db

# UTC+8 timezone for display
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

class SupportTicket(db.Model):
    __tablename__ = "support_tickets"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    status = db.Column(db.String(16), default="open", nullable=False)
    created = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, onupdate=datetime.utcnow)

class SupportMessage(db.Model):
    __tablename__ = "support_messages"
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("support_tickets.id"), index=True, nullable=False)
    sender_role = db.Column(db.String(16), nullable=False)  # "user" | "admin"
    sender_id = db.Column(db.Integer, nullable=False)
    text = db.Column(db.Text, nullable=False)
    created = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self):
        # Convert timestamp to UTC+8 for consistent display
        display_time = format_datetime_for_display(self.created)
        
        return {
            "id": self.id,
            "ticket_id": self.ticket_id,
            "sender_role": self.sender_role,
            "sender_id": self.sender_id,
            "text": self.text,
            # Send timezone-adjusted timestamp (no Z suffix since it's already converted)
            "created": display_time.isoformat() if display_time else None,
        }

class UserNotification(db.Model):
    __tablename__ = "user_notifications"
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False, index=True)
    last_seen_message_id = db.Column(db.Integer, default=0, nullable=False)
    unread_admin_count = db.Column(db.Integer, default=0, nullable=False)
    created = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f"<UserNotification user_id={self.user_id} ticket_id={self.ticket_id} unread={self.unread_admin_count}>"