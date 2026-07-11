// 历史目标「一键复用」: 把某版本字段填入当前目标表单, 由用户确认后再保存。
// 只填表单, 不落库; 保存仍走现有 /api/goals, 版本化不覆盖历史。
(function () {
  const hint = document.getElementById('reuse-hint');
  const form = document.getElementById('current-goal-form');
  if (!form) return;

  document.querySelectorAll('.gv-reuse').forEach(function (btn) {
    btn.addEventListener('click', function () {
      const d = btn.dataset;
      document.getElementById('g-primary').value = d.primary || 'custom';
      document.getElementById('g-running').value = d.running || '';
      document.getElementById('g-strength').value = d.strength || '';
      document.getElementById('g-conflict').value = d.conflict || '';
      document.getElementById('g-uncertain').value = d.uncertain || '';
      // 生效日期不复用旧值, 留给用户按新意图填
      if (hint) {
        hint.style.display = '';
        hint.textContent = '已载入 v' + d.version + ' 的配置到上方表单,可修改后点「保存为新版本」。历史版本不受影响。';
      }
      form.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  });
})();
