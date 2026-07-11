// AI 目标澄清对话: 消息只存前端内存, 不落库。
(function () {
  const chat = document.getElementById('chat');
  const form = document.getElementById('chat-form');
  const textEl = document.getElementById('chat-text');
  const sendBtn = document.getElementById('send-btn');
  const draftBtn = document.getElementById('draft-btn');
  if (!chat || !form) return;

  const messages = []; // {role, content}

  function bubble(role, content) {
    const el = document.createElement('div');
    el.className = 'bubble ' + (role === 'user' ? 'me' : 'ai');
    // 用户输入永远只用 textContent (不渲染 HTML); AI 回复由后端返回已净化的 HTML 时才用 innerHTML
    el.textContent = content;
    chat.appendChild(el);
    chat.scrollTop = chat.scrollHeight;
    return el;
  }

  function setBusy(busy) {
    sendBtn.disabled = busy;
    draftBtn.disabled = busy;
    textEl.disabled = busy;
  }

  // 开场白
  bubble('assistant', '你好,我来帮你把训练目标理清楚。先说说你最近最想达成的是什么?比如某场比赛、想变强、还是先恢复。');

  form.addEventListener('submit', async function (e) {
    e.preventDefault();
    const content = (textEl.value || '').trim();
    if (!content) return;
    bubble('user', content);
    messages.push({ role: 'user', content: content });
    textEl.value = '';
    setBusy(true);
    const thinking = bubble('assistant', '…');
    try {
      const resp = await fetch('/api/goals/clarify/message', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messages: messages }),
      });
      const data = await resp.json();
      if (data.ok) {
        // reply_html 由后端 escape-first 渲染, 安全; 回退到纯文本
        thinking.className = 'bubble ai md-body';
        if (data.reply_html) { thinking.innerHTML = data.reply_html; }
        else { thinking.textContent = data.reply; }
        messages.push({ role: 'assistant', content: data.reply });
        chat.scrollTop = chat.scrollHeight;
      } else {
        thinking.className = 'bubble ai err';
        thinking.textContent = '出错了: ' + (data.message || '未知错误');
      }
    } catch (err) {
      thinking.className = 'bubble ai err';
      thinking.textContent = '请求失败: ' + err;
    } finally {
      setBusy(false);
      textEl.focus();
    }
  });

  draftBtn.addEventListener('click', async function () {
    if (messages.length === 0) {
      alert('先聊几句再汇总吧。');
      return;
    }
    setBusy(true);
    const prev = draftBtn.textContent;
    draftBtn.textContent = '汇总中…';
    try {
      const resp = await fetch('/api/goals/clarify/draft', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messages: messages }),
      });
      const data = await resp.json();
      if (data.ok) {
        fillDraft(data.draft);
      } else {
        alert('汇总失败: ' + (data.message || '未知错误'));
      }
    } catch (err) {
      alert('请求失败: ' + err);
    } finally {
      setBusy(false);
      draftBtn.textContent = prev;
    }
  });

  function fillDraft(d) {
    document.getElementById('draft-card').style.display = '';
    document.getElementById('d-primary').value = d.primary_goal || 'custom';
    document.getElementById('d-running').value = d.running_goal_text || '';
    document.getElementById('d-strength').value = d.strength_baseline_text || '';
    document.getElementById('d-conflict').value = d.conflict_policy_text || '';
    document.getElementById('d-uncertain').value = d.uncertainties_text || '';
    const hint = document.getElementById('rest-hint');
    if (d.rest_note_hint) {
      hint.style.display = '';
      hint.innerHTML = '这段听起来更像「过去发生的休整事件」: ' +
        d.rest_note_hint.replace(/</g, '&lt;') +
        ' — 建议去 <a href="/rest-notes">休整标注</a> 单独记一笔,别混进目标里。';
    } else {
      hint.style.display = 'none';
    }
    document.getElementById('draft-card').scrollIntoView({ behavior: 'smooth' });
  }
})();
