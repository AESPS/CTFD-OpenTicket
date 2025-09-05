/* support.js — Updated with consistent UTC+8 timezone formatting */
(function () {
  // Only on /challenges and prevent duplicate injections
  if (!location.pathname.startsWith("/challenges")) return;
  if (document.getElementById("sw-widget")) return;

  // If an old widget id was left around from a previous version, remove it
  const legacy = document.getElementById("support-widget");
  if (legacy) legacy.remove();

  // ---------- DOM (matches support.css) ----------
  const root = document.createElement("div");
  root.id = "sw-widget";
  root.innerHTML = `
    <button class="sw-button" id="sw-open">
      Got Questions? <span id="sw-dot" class="sw-dot" style="display:none;"></span>
      <span id="sw-notification" class="sw-notification" style="display:none;">0</span>
    </button>

    <div class="sw-panel" id="sw-panel" aria-hidden="true">
      <div class="sw-header">
        <div><strong>Support</strong></div>
        <div class="sw-actions">
          <button class="sw-pill" id="sw-close" title="Close chat">Close</button>
        </div>
      </div>

      <div class="sw-msgs" id="sw-msgs">
        <div class="sw-small" id="sw-empty">Start a conversation—your messages appear here.</div>
      </div>

      <div class="sw-footer">
        <div class="sw-inputrow">
          <input id="sw-input" type="text" placeholder="Type your message..." autocomplete="off">
          <button id="sw-send">Send</button>
        </div>

        <div class="sw-small" id="sw-hint">
          Ask your questions, admin will reply here.
        </div>
      </div>
    </div>
  `;
  document.body.appendChild(root);

  // ---------- Elements ----------
  const openBtn = document.getElementById("sw-open");
  const panel   = document.getElementById("sw-panel");
  const closeBtn= document.getElementById("sw-close");
  const list    = document.getElementById("sw-msgs");
  const empty   = document.getElementById("sw-empty");
  const input   = document.getElementById("sw-input");
  const sendBtn = document.getElementById("sw-send");
  const hint    = document.getElementById("sw-hint");
  const dot     = document.getElementById("sw-dot");
  const notification = document.getElementById("sw-notification");

  // ---------- State ----------
  let ticketId = null;
  let hasTicket = false; // Track if user has ticket
  let pollTimer = null;
  let notificationTimer = null;
  let lastSeenMsgId = null;
  let cachedNonce = null;
  let unreadCount = 0;
  let isPolling = false;
  let lastUnreadCount = 0;

  // ---------- Helpers ----------
  function esc(s) {
    return (s || "").replace(/[&<>"]/g, (c) => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", "\"":"&quot;" }[c]));
  }

  // UTC+8 date formatting function - server already sends converted timestamps
  function formatDateUTC8(dateStr) {
    if (!dateStr) return '';
    try {
      // Server already sends UTC+8 converted timestamps, so just format
      const date = new Date(dateStr);
      return date.toLocaleString('en-GB', {
        hour12: false,
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit'
      }).replace(',', '');
    } catch (e) {
      console.error('Date formatting error:', e);
      return dateStr;
    }
  }

  function hasNonEnglishContent(text) {
    if (!text) return false;
    
    // Check for non-Latin characters (Thai, Khmer, etc.)
    if (/[\u0e00-\u0e7f\u1780-\u17ff\u4e00-\u9fff]/.test(text)) return true;
    
    // Check for common Southeast Asian words
    const foreignWords = [
      'saya', 'anda', 'dengan', 'untuk', 'dari', 'tidak', 'yang', 'ada', 'dia', // Malay/Indonesian
      'tôi', 'bạn', 'với', 'để', 'từ', 'không', 'mà', 'có', 'anh', 'chị', // Vietnamese  
      'ผม', 'คุณ', 'กับ', 'เพื่อ', 'จาก', 'ไม่', 'ที่', 'มี', 'เขา', // Thai (romanized)
      'ขอบคุณ', 'สวัสดี', 'ช่วย', 'ปัญหา', // More Thai
      'terima kasih', 'selamat', 'tolong', 'masalah', 'bagaimana', // More Malay/Indonesian
      'xin chào', 'cảm ơn', 'giúp', 'vấn đề', 'như thế nào' // More Vietnamese
    ];
    
    const lowerText = text.toLowerCase();
    return foreignWords.some(word => lowerText.includes(word));
  }

  function updateNotification(count) {
    unreadCount = count;
    if (count > 0) {
      notification.textContent = count > 99 ? '99+' : count;
      notification.style.display = 'inline-block';
      dot.style.display = 'none';
      
      // Add pulse animation for new notifications
      notification.classList.add('sw-pulse');
      setTimeout(() => {
        notification.classList.remove('sw-pulse');
      }, 2000);
    } else {
      notification.style.display = 'none';
      dot.style.display = 'none';
    }
  }

  function clearNotifications() {
    updateNotification(0);
    lastUnreadCount = 0;
  }

  async function getNonce() {
    if (typeof window.csrf_token === "string" && window.csrf_token) return window.csrf_token;
    if (cachedNonce) return cachedNonce;
    try {
      const r = await fetch("/support/nonce", { credentials: "same-origin" });
      if (!r.ok) return "";
      const d = await r.json();
      cachedNonce = d.nonce || "";
      return cachedNonce;
    } catch { return ""; }
  }

  async function markAsRead() {
    // Only mark as read if user has a ticket
    if (!hasTicket) return;
    
    try {
      const nonce = await getNonce();
      const r = await fetch("/support/mark_read", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: `nonce=${encodeURIComponent(nonce)}`,
        credentials: "same-origin"
      });
      if (r.ok) {
        const d = await r.json();
        if (d.ok) {
          clearNotifications();
        }
      }
    } catch (error) {
      console.error("Failed to mark messages as read:", error);
    }
  }

  async function checkUnreadCount() {
    // Only check if panel is closed
    if (panel.getAttribute("aria-hidden") !== "true") return;
    
    try {
      const r = await fetch("/support/unread_count", { credentials: "same-origin" });
      if (r.ok) {
        const d = await r.json();
        const count = d.unread_count || 0;
        
        // Show browser notification for NEW messages only
        if (count > lastUnreadCount && count > 0 && "Notification" in window && Notification.permission === "granted") {
          const newMessages = count - lastUnreadCount;
          new Notification("Support Chat", {
            body: `You have ${newMessages} new message${newMessages > 1 ? 's' : ''} from admin`,
            icon: "/themes/core/static/img/logo.png",
            tag: "support-chat"
          });
        }
        
        updateNotification(count);
        lastUnreadCount = count;
      }
    } catch (error) {
      console.error("Failed to check unread count:", error);
    }
  }

  function bubbleHTML(m, isNewMessage = false) {
    const mine = m.sender_role !== "admin";
    const cls  = mine ? "sw-user" : "sw-admin";
    const who  = mine ? "You" : "Admin";
    const ts   = formatDateUTC8(m.created); // Use UTC+8 formatting
    const id   = `b-${m.id || (Math.random()+"").slice(2)}`;
    const txt  = esc(m.text);
    
    // Add animation class for new messages
    const animClass = isNewMessage ? ' sw-new-message' : '';
    
    // Only show translate link if text contains non-English characters or common foreign words
    const needsTranslation = hasNonEnglishContent(m.text);
    const translateLink = needsTranslation ? 
      `<div class="sw-small"><a href="#" class="sw-toggle-tr" data-target="${id}" data-state="original">Translate to English</a></div>` : '';
    
    return `
      <div class="sw-msg${animClass}" data-id="${m.id || ""}" data-role="${m.sender_role}">
        <div class="sw-meta">${who} <span style="opacity:.7">${ts}</span></div>
        <div class="sw-bubble ${cls}" id="${id}" data-original="${txt}">${txt}</div>
        ${translateLink}
      </div>
    `;
  }

  function render(messages, highlightNew = false) {
    const oldLastSeenId = lastSeenMsgId;
    list.innerHTML = "";
    
    if (!messages || !messages.length) {
      empty.style.display = "block";
      return;
    }
    
    empty.style.display = "none";
    messages.forEach(m => {
      const isNewMessage = highlightNew && oldLastSeenId && m.id > oldLastSeenId;
      list.insertAdjacentHTML("beforeend", bubbleHTML(m, isNewMessage));
    });
    
    list.scrollTop = list.scrollHeight;
    const last = messages[messages.length - 1];
    lastSeenMsgId = last && last.id ? last.id : lastSeenMsgId;
  }

  function flipToggle(aEl, showingTranslated) {
    aEl.textContent = showingTranslated ? "Show original" : "Translate to English";
    aEl.setAttribute("data-state", showingTranslated ? "translated" : "original");
  }

  async function translateText(text, target="en") {
    try {
      const nonce = await getNonce();
      
      const r = await fetch("/support/translate", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: `text=${encodeURIComponent(text)}&target=${encodeURIComponent(target)}&nonce=${encodeURIComponent(nonce)}`,
        credentials: "same-origin"
      });
      
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      
      const d = await r.json();
      
      if (d && d.ok && d.translated) {
        return d.translated;
      }
      
      if (d && d.error) {
        console.warn("Translation error:", d.error);
      }
      
      return text;
    } catch (error) {
      console.error("Translation request failed:", error);
      return text;
    }
  }

  async function loadTicket() {
    try {
      const r = await fetch("/support/ticket", { credentials: "same-origin" });
      if (r.status === 401) {
        list.innerHTML = `<div class="sw-small">Please log in to use support.</div>`;
        return;
      }
      const d = await r.json();
      
      // Check if user has a ticket
      hasTicket = d.ticket_id !== null;
      ticketId = d.ticket_id;
      
      if (hasTicket) {
        render(d.messages || []);
        
        // Update unread count from server response - but only if panel is closed
        const serverUnreadCount = d.unread_admin_count || 0;
        if (panel.getAttribute("aria-hidden") === "true") {
          updateNotification(serverUnreadCount);
          lastUnreadCount = serverUnreadCount;
        }
      } else {
        // No ticket yet - show empty state
        render([]);
      }
    } catch (error) {
      console.error("Failed to load ticket:", error);
    }
  }

  function startPolling() {
    if (pollTimer) clearInterval(pollTimer);
    isPolling = true;
    
    pollTimer = setInterval(async () => {
      try {
        const r = await fetch("/support/ticket", { credentials: "same-origin" });
        const d = await r.json();
        
        // Update ticket status
        hasTicket = d.ticket_id !== null;
        ticketId = d.ticket_id;
        
        if (!hasTicket) {
          // No ticket yet, no messages to show
          return;
        }
        
        const msgs = d.messages || [];
        if (!msgs.length) return;
        
        const last = msgs[msgs.length - 1];
        const panelClosed = panel.getAttribute("aria-hidden") === "true";
        
        // Update messages if there are changes
        if (last && last.id !== lastSeenMsgId) {
          render(msgs, panelClosed);
          
          // If panel is closed and there are new admin messages, show notification
          if (panelClosed) {
            const serverUnreadCount = d.unread_admin_count || 0;
            if (serverUnreadCount > lastUnreadCount) {
              // Show browser notification for new messages
              if ("Notification" in window && Notification.permission === "granted") {
                new Notification("Support Chat", {
                  body: "Admin replied to your support ticket",
                  icon: "/themes/core/static/img/logo.png",
                  tag: "support-chat"
                });
              }
            }
            updateNotification(serverUnreadCount);
            lastUnreadCount = serverUnreadCount;
          }
        }
      } catch (error) {
        console.error("Polling error:", error);
      }
    }, 4000);
  }

  function startNotificationChecking() {
    if (notificationTimer) clearInterval(notificationTimer);
    
    // Check for unread messages every 15 seconds when chat is closed
    notificationTimer = setInterval(() => {
      if (panel.getAttribute("aria-hidden") === "true" && hasTicket) {
        checkUnreadCount();
      }
    }, 15000);
  }

  function openPanel() {
    panel.classList.add("sw-open");
    panel.setAttribute("aria-hidden", "false");
    panel.style.display = "flex";
    openBtn.classList.add("hidden");
    
    // Clear notifications when panel is opened
    clearNotifications();
    
    // Mark messages as read after a short delay (only if user has ticket)
    if (hasTicket) {
      setTimeout(() => {
        markAsRead();
      }, 1000);
    }
  }

  function closePanel() {
    panel.classList.remove("sw-open");
    panel.setAttribute("aria-hidden", "true");
    panel.style.display = "none";
    openBtn.classList.remove("hidden");
    
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
      isPolling = false;
    }
  }

  // ---------- Events ----------
  openBtn.addEventListener("click", async () => {
    document.querySelectorAll("#sw-widget .sw-panel").forEach(p => {
      p.classList.remove("sw-open");
      p.setAttribute("aria-hidden","true");
      p.style.display = "none";
    });
    
    openPanel();
    await loadTicket();
    startPolling();
    
    // Request notification permission
    if ("Notification" in window && Notification.permission === "default") {
      Notification.requestPermission();
    }
  });

  closeBtn.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    closePanel();
  });

  document.addEventListener("click", (e) => {
    if (panel.classList.contains("sw-open") && 
        !panel.contains(e.target) && 
        !openBtn.contains(e.target)) {
      closePanel();
    }
  });

  panel.addEventListener("click", (e) => {
    e.stopPropagation();
  });

  sendBtn.addEventListener("click", async () => {
    const text = input.value.trim();
    if (!text) return;
    
    const nonce = await getNonce();
    try {
      const r = await fetch("/support/message", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: `text=${encodeURIComponent(text)}&nonce=${encodeURIComponent(nonce)}`,
        credentials: "same-origin"
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || d.ok === false) {
        hint.textContent = "Failed to send. Try again.";
        hint.style.color = "#ffb3b3";
        return;
      }
      
      // Message sent successfully - user now has a ticket
      hasTicket = true;
      
      input.value = "";
      hint.textContent = "Ask your questions, admin will reply here.";
      hint.style.color = "";
      await loadTicket();
    } catch {
      hint.textContent = "Failed to send (network).";
      hint.style.color = "#ffb3b3";
    }
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") sendBtn.click();
  });

  // Per-message toggle (Translate ↔ Original)
  list.addEventListener("click", async (e) => {
    const a = e.target.closest(".sw-toggle-tr");
    if (!a) return;
    e.preventDefault();

    const id = a.getAttribute("data-target");
    const bubble = document.getElementById(id);
    if (!bubble) return;

    const original = bubble.getAttribute("data-original") || bubble.textContent || "";
    const state = a.getAttribute("data-state");

    if (state === "original") {
      a.textContent = "Translating...";
      
      const translated = await translateText(original, "en");
      
      if (translated !== original && translated.trim() !== original.trim()) {
        bubble.textContent = translated;
        flipToggle(a, true);
      } else {
        a.parentElement.style.display = 'none';
      }
    } else {
      bubble.textContent = original;
      flipToggle(a, false);
    }
  });

  // Initialize with CLOSED state and start notification checking
  closePanel();
  startNotificationChecking();
  
  // Initial unread count check after a short delay (only if user has ticket)
  setTimeout(() => {
    checkUnreadCount();
  }, 2000);
})();