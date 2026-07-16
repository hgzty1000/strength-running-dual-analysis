// 单日分享卡片 → PNG 导出 (v0.3.1)
// 依赖 vendor/snapdom.min.js (window.snapdom); 仅捕获 #poster 海报本体,
// 工具栏在海报之外,不会进图,不新增隐私面。
(function () {
  "use strict";

  var btn = document.getElementById("save-png-button");
  var poster = document.getElementById("poster");
  if (!btn || !poster) return;

  // snapdom 未加载(离线/脚本失败)时,禁用按钮并给出兜底提示
  if (typeof window.snapdom === "undefined") {
    btn.disabled = true;
    btn.textContent = "导出不可用";
    btn.title = "图片导出组件未加载,可长按卡片手动截图";
    return;
  }

  var defaultLabel = btn.textContent;

  function buildFilename() {
    var date = poster.getAttribute("data-share-date") || "训练";
    var theme = poster.getAttribute("data-share-theme") || "";
    var name = "力跑双训_" + date + (theme ? "_" + theme : "");
    return name;
  }

  // 窄屏下 #poster 的祖先 #poster-scale 带 transform: scale(); 导出前临时归位,
  // 确保 snapdom 无论用 getBoundingClientRect 还是 offsetWidth 测量, 都拿到 450×600
  // 原始逻辑尺寸, 导出图不被连带缩小。拍完(无论成败)恢复原缩放。
  var scaleEl = document.getElementById("poster-scale");

  function freezeScale() {
    if (!scaleEl) return null;
    var prev = scaleEl.style.transform;
    scaleEl.style.transform = "none";
    return prev;
  }

  function restoreScale(prev) {
    if (!scaleEl) return;
    scaleEl.style.transform = prev || "";
  }

  btn.addEventListener("click", function () {
    if (btn.disabled) return;
    btn.disabled = true;
    btn.textContent = "生成中…";

    var prevTransform = freezeScale();

    // scale:3 出高清图,适合发群/朋友圈; backgroundColor 保留主题自身背景
    window.snapdom
      .download(poster, {
        format: "png",
        filename: buildFilename(),
        scale: 3,
      })
      .then(function () {
        restoreScale(prevTransform);
        btn.textContent = "已保存";
        setTimeout(function () {
          btn.textContent = defaultLabel;
          btn.disabled = false;
        }, 1600);
      })
      .catch(function (err) {
        restoreScale(prevTransform);
        console.error("分享卡片导出失败", err);
        btn.textContent = "生成失败";
        btn.title = "生成失败,可长按卡片手动截图";
        setTimeout(function () {
          btn.textContent = defaultLabel;
          btn.disabled = false;
        }, 2200);
      });
  });
})();
