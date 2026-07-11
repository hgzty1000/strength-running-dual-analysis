// 报告浅追问: 就当前报告快照提问, 问答落库并渲染。
(function () {
  const form = document.getElementById('fu-form');
  const list = document.getElementById('fu-list');
  const textEl = document.getElementById('fu-text');
  const btn = document.getElementById('fu-btn');
  if (!form || !list) return;
  const reportId = form.dataset.report;

  function addItem(question) {
    const item = document.createElement('div');
    item.className = 'fu-item';
    const q = document.createElement('div');
    q.className = 'bubble me fu-q';
    q.textContent = question;              // 用户输入只用 textContent
    const a = document.createElement('div');
    a.className = 'bubble ai md-body';
    a.textContent = '…';
    item.appendChild(q);
    item.appendChild(a);
    list.appendChild(item);
    a.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    return a;
  }

  form.addEventListener('submit', async function (e) {
    e.preventDefault();
    const question = (textEl.value || '').trim();
    if (!question) return;
    const answerEl = addItem(question);
    textEl.value = '';
    btn.disabled = true; textEl.disabled = true;
    try {
      const resp = await fetch('/api/reports/' + reportId + '/followup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: question }),
      });
      const data = await resp.json();
      if (data.ok) {
        if (data.answer_html) { answerEl.innerHTML = data.answer_html; }  // 后端已净化
        else { answerEl.textContent = data.answer; }
      } else {
        answerEl.className = 'bubble ai err';
        answerEl.textContent = '出错了: ' + (data.message || '未知错误');
      }
    } catch (err) {
      answerEl.className = 'bubble ai err';
      answerEl.textContent = '请求失败: ' + err;
    } finally {
      btn.disabled = false; textEl.disabled = false; textEl.focus();
    }
  });
})();
