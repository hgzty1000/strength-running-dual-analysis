// 分享卡片窄屏自适应 (v0.3.1)
// 海报按固定 450×600 逻辑尺寸绘制; 窄屏放不下时整体等比缩小显示,
// 而非压缩内部布局 (那会触发 overflow:hidden 裁切内容)。
// #poster 自身尺寸不变, snapdom 导出仍是完整的 450×600, 手机上只是缩小预览。
(function () {
  "use strict";

  var POSTER_W = 450;
  var POSTER_H = 600;

  var shell = document.querySelector(".share-shell");
  var frame = document.getElementById("poster-frame");
  var scale = document.getElementById("poster-scale");
  if (!shell || !frame || !scale) return;

  function fit() {
    // 可用宽度 = shell 内容宽 (已扣 padding); 不放大, 只在放不下时缩小
    var avail = shell.clientWidth;
    var s = Math.min(1, avail / POSTER_W);
    scale.style.transform = "scale(" + s + ")";
    // frame 占位到缩放后的真实尺寸, 保证居中与工具栏不遮挡卡片底部
    frame.style.width = POSTER_W * s + "px";
    frame.style.height = POSTER_H * s + "px";
  }

  fit();
  window.addEventListener("resize", fit);
  window.addEventListener("orientationchange", fit);
})();
