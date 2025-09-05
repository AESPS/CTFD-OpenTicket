/* admin.js — Updated with consistent UTC+8 timezone formatting */
(function () {
  if (!location.pathname.startsWith("/support/admin")) return;

  let cachedNonce = null;
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

  function esc(s){ return (s||"").replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

  // UTC+8 date formatting function - handles timestamps already converted by server
  function formatDate(dateStr) {
    if (!dateStr || dateStr === 'N/A') return 'N/A';
    try {
      // Server already sends UTC+8 converted timestamps, so just parse and format
      const date = new Date(dateStr);
      
      // Format as MM/DD HH:MM
      return String(date.getMonth() + 1).padStart(2, '0') + '/' + 
             String(date.getDate()).padStart(2, '0') + ' ' +
             String(date.getHours()).padStart(2, '0') + ':' +
             String(date.getMinutes()).padStart(2, '0');
    } catch (e) {
      console.error('Date formatting error:', e);
      return 'Invalid Date';
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

  const detail = document.querySelector("#sc-admin-detail");

  // Render a ticket thread in the improved layout
  function renderThread(ticket) {
    const msgs = ticket.messages || [];
    const user = ticket.user || {};
    const userName = user.name || "Unknown User";
    const userEmail = user.email || "";
    const teamName = user.team_name || null;
    
    // Format user display with team
    let userDisplay = userName;
    if (teamName) {
      userDisplay = `${userName} (Team ${teamName})`;
    }
    
    const header = `
      <div class="ticket-header">
        <div class="d-flex justify-content-between align-items-center">
          <div>
            <h4 class="mb-1">
              <i class="fas fa-ticket-alt mr-2"></i>Ticket #${ticket.id}
            </h4>
            <div class="d-flex align-items-center">
              <span class="badge badge-${ticket.status === 'open' ? 'success' : 'light'} mr-2">
                ${esc(ticket.status)}
              </span>
              <i class="fas fa-user mr-1"></i>
              <span>${esc(userDisplay)}</span>
              ${userEmail ? `<small class="ml-2 opacity-75">${esc(userEmail)}</small>` : ''}
            </div>
          </div>
          <div class="d-flex" style="gap: 0.5rem;">
            ${ticket.status === 'open' ? 
              `<button class="btn btn-warning btn-sm" id="sc-close" data-id="${ticket.id}">
                <i class="fas fa-times mr-1"></i>Close
              </button>` : ''
            }
            <button class="btn btn-danger btn-sm" id="sc-delete" data-id="${ticket.id}" title="Permanently delete this ticket">
              <i class="fas fa-trash mr-1"></i>Delete
            </button>
          </div>
        </div>
      </div>
    `;

    const messagesHtml = msgs.map(m => {
      const isAdmin = m.sender_role === "admin";
      let role = isAdmin ? "Admin" : "User";
      
      // Show username and team for user messages
      if (!isAdmin && m.sender_name) {
        role = m.sender_name;
        if (m.sender_team) {
          role = `${m.sender_name} (Team ${m.sender_team})`;
        }
      }
      
      const messageClass = isAdmin ? "message-admin" : "message-user";
      const textId = `adm-b-${m.id || (Math.random()+"").slice(2)}`;
      
      // Use consistent date formatting (server already provides UTC+8)
      const timestamp = formatDate(m.created);
      
      // Only show translate link if content needs translation
      const needsTranslation = hasNonEnglishContent(m.text);
      const translateLink = needsTranslation ? 
        `<div class="mt-1">
          <span class="sc-adm-tr translate-link" data-target="${textId}" data-state="original">
            Translate to English
          </span>
        </div>` : '';
      
      return `
        <div class="d-flex ${isAdmin ? 'justify-content-start' : 'justify-content-end'}">
          <div class="message-bubble ${messageClass}">
            <div class="message-meta">
              <i class="fas fa-${isAdmin ? 'user-shield' : 'user'} mr-1"></i>
              ${role} • ${timestamp}
            </div>
            <div id="${textId}" data-original="${esc(m.text)}">${esc(m.text)}</div>
            ${translateLink}
          </div>
        </div>
      `;
    }).join("");

    const messagesContainer = `
      <div class="ticket-messages">
        ${messagesHtml || '<div class="text-center text-muted"><i class="fas fa-comments fa-2x mb-2"></i><br>No messages yet</div>'}
      </div>
    `;

    const replySection = ticket.status === 'open' ? `
      <div class="reply-section">
        <div class="input-group">
          <div class="input-group-prepend">
            <span class="input-group-text">
              <i class="fas fa-reply"></i>
            </span>
          </div>
          <input id="sc-reply" type="text" class="form-control" placeholder="Type your admin reply here..." autocomplete="off">
          <div class="input-group-append">
            <button class="btn btn-primary" id="sc-send" data-id="${ticket.id}">
              <i class="fas fa-paper-plane mr-1"></i>Send
            </button>
          </div>
        </div>
        <small class="text-muted mt-1 d-block" id="sc-hint">
          <i class="fas fa-info-circle mr-1"></i>Your reply will be sent instantly to the user's chat widget.
        </small>
      </div>
    ` : `
      <div class="ticket-closed">
        <i class="fas fa-lock mr-2"></i>This ticket has been closed
      </div>
    `;

    detail.innerHTML = header + messagesContainer + replySection;
    
    // Auto-scroll to bottom of messages
    const messagesDiv = detail.querySelector('.ticket-messages');
    if (messagesDiv) {
      messagesDiv.scrollTop = messagesDiv.scrollHeight;
    }
  }

  async function openTicket(id) {
    // Show loading state
    detail.innerHTML = `
      <div class="d-flex align-items-center justify-content-center" style="height: 400px;">
        <div class="text-center">
          <div class="spinner-border text-primary" role="status">
            <span class="sr-only">Loading...</span>
          </div>
          <p class="mt-2 text-muted">Loading ticket...</p>
        </div>
      </div>
    `;

    try {
      const r = await fetch(`/support/admin/ticket/${encodeURIComponent(id)}`, { 
        credentials: "same-origin" 
      });
      
      if (!r.ok) throw new Error("load");
      const d = await r.json();
      renderThread(d.ticket);
    } catch {
      detail.innerHTML = `
        <div class="card-body d-flex align-items-center justify-content-center">
          <div class="text-center text-danger">
            <i class="fas fa-exclamation-triangle fa-3x mb-3"></i>
            <h5>Failed to load ticket #${id}</h5>
            <p class="text-muted">Please try refreshing the page or contact support.</p>
          </div>
        </div>
      `;
    }
  }

  async function translateText(text, target="en") {
    try {
      const nonce = await getNonce();
      
      const r = await fetch("/support/translate", {
        method: "POST",
        headers: {"Content-Type":"application/x-www-form-urlencoded"},
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

  // Delegated clicks from the ticket list
  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-open-ticket]");
    if (btn) {
      e.preventDefault();
      const id = btn.getAttribute("data-open-ticket");
      
      // Highlight selected ticket
      document.querySelectorAll('.ticket-item').forEach(item => {
        item.style.backgroundColor = 'white';
        item.style.borderLeft = 'none';
      });
      
      const ticketItem = btn.closest('.ticket-item');
      if (ticketItem) {
        ticketItem.style.backgroundColor = '#e3f2fd';
        ticketItem.style.borderLeft = '4px solid #007bff';
      }
      
      openTicket(id);
    }
  });

  // All other event handlers remain the same...
  detail.addEventListener("click", async (e) => {
    // Handle translation
    const tr = e.target.closest(".sc-adm-tr");
    if (tr) {
      e.preventDefault();
      const target = tr.getAttribute("data-target");
      const bubble = document.getElementById(target);
      if (!bubble) return;
      
      const original = bubble.getAttribute("data-original") || bubble.textContent || "";
      const state = tr.getAttribute("data-state");
      
      if (state === "original") {
        tr.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Translating...';
        
        const translated = await translateText(original, "en");
        
        if (translated !== original && translated.trim() !== original.trim()) {
          bubble.textContent = translated;
          tr.textContent = "Show original";
          tr.setAttribute("data-state","translated");
        } else {
          tr.parentElement.style.display = 'none';
        }
      } else {
        bubble.textContent = original;
        tr.textContent = "Translate to English";
        tr.setAttribute("data-state","original");
      }
      return;
    }

    // Handle send reply
    const send = e.target.closest("#sc-send");
    if (send) {
      e.preventDefault();
      const id = send.getAttribute("data-id");
      const input = detail.querySelector("#sc-reply");
      const text = (input.value || "").trim();
      const hint = detail.querySelector("#sc-hint");
      
      if (!text) {
        hint.innerHTML = '<i class="fas fa-exclamation-triangle mr-1 text-warning"></i>Please enter a message.';
        input.focus();
        return;
      }
      
      // Show sending state
      send.disabled = true;
      send.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>Sending...';
      
      const nonce = await getNonce();
      try {
        const r = await fetch("/support/admin/reply", {
          method: "POST",
          headers: {"Content-Type":"application/x-www-form-urlencoded"},
          body: `ticket_id=${encodeURIComponent(id)}&text=${encodeURIComponent(text)}&nonce=${encodeURIComponent(nonce)}`,
          credentials: "same-origin"
        });
        
        const d = await r.json().catch(()=>({}));
        if (!r.ok || d.ok === false) throw new Error("send");
        
        input.value = "";
        hint.innerHTML = '<i class="fas fa-check text-success mr-1"></i>Message sent successfully!';
        
        // Reload the ticket to show the new message
        setTimeout(() => openTicket(id), 500);
        
      } catch {
        hint.innerHTML = '<i class="fas fa-exclamation-triangle mr-1 text-danger"></i>Failed to send message. Please try again.';
      } finally {
        send.disabled = false;
        send.innerHTML = '<i class="fas fa-paper-plane mr-1"></i>Send';
      }
      return;
    }

    // Handle close ticket
    const close = e.target.closest("#sc-close");
    if (close) {
      e.preventDefault();
      const id = close.getAttribute("data-id");
      
      if (!confirm("Are you sure you want to close this ticket?")) return;
      
      close.disabled = true;
      close.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>Closing...';
      
      const nonce = await getNonce();
      try {
        const r = await fetch("/support/admin/close", {
          method: "POST",
          headers: {"Content-Type":"application/x-www-form-urlencoded"},
          body: `ticket_id=${encodeURIComponent(id)}&nonce=${encodeURIComponent(nonce)}`,
          credentials: "same-origin"
        });
        
        const d = await r.json().catch(()=>({}));
        if (!r.ok || d.ok === false) throw new Error("close");
        
        // Reload the page to update the ticket list
        window.location.reload();
        
      } catch {
        alert("Failed to close the ticket. Please try again.");
        close.disabled = false;
        close.innerHTML = '<i class="fas fa-times mr-1"></i>Close Ticket';
      }
      return;
    }

    // Handle delete ticket
    const deleteBtn = e.target.closest("#sc-delete");
    if (deleteBtn) {
      e.preventDefault();
      const id = deleteBtn.getAttribute("data-id");
      
      if (!confirm("Are you sure you want to PERMANENTLY DELETE this ticket?\n\nThis will remove:\n• The ticket\n• All messages\n• All conversation history\n\nThis action CANNOT be undone!")) return;
      
      deleteBtn.disabled = true;
      deleteBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>Deleting...';
      
      const nonce = await getNonce();
      try {
        const r = await fetch("/support/admin/delete", {
          method: "POST",
          headers: {"Content-Type":"application/x-www-form-urlencoded"},
          body: `ticket_id=${encodeURIComponent(id)}&nonce=${encodeURIComponent(nonce)}`,
          credentials: "same-origin"
        });
        
        const d = await r.json().catch(()=>({}));
        if (!r.ok || d.ok === false) throw new Error("delete");
        
        // Show success message and reload page
        alert("Ticket deleted successfully!");
        window.location.reload();
        
      } catch {
        alert("Failed to delete the ticket. Please try again.");
        deleteBtn.disabled = false;
        deleteBtn.innerHTML = '<i class="fas fa-trash mr-1"></i>Delete Ticket';
      }
      return;
    }
  });

  // Handle Enter key in reply input
  detail.addEventListener("keydown", (e) => {
    if (e.target.id === "sc-reply" && e.key === "Enter") {
      const sendBtn = detail.querySelector("#sc-send");
      if (sendBtn && !sendBtn.disabled) {
        sendBtn.click();
      }
    }
  });

  // Optional: automatically open a ticket if URL has ticket_id parameter
  const urlParams = new URLSearchParams(window.location.search);
  const preselectedTicket = urlParams.get("ticket_id");
  if (preselectedTicket) {
    openTicket(preselectedTicket);
  }
})();