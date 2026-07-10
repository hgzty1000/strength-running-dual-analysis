# Garmin ZIP 重命名工具

一个本地离线小工具,用于整理从 Garmin 手工下载的活动 ZIP 文件。

它读取 ZIP 内的 `.fit` 活动文件,提取活动开始时间和运动类型,然后把 ZIP 文件本身重命名为可读、可排序的名字。

## 边界

这个工具是独立 PC 小工具,不是网页平台的一部分。

它会做:

- 本地扫描 ZIP 文件;
- 读取 ZIP 内唯一的 `.fit` 文件;
- 从 FIT 中解析活动开始时间、运动类型、距离/时长摘要;
- 扫描当前目录内重复活动;
- 预览新文件名;
- 经确认后重命名 ZIP 文件本身。

它不会做:

- 不联网;
- 不连接平台;
- 不维护下载账本/数据库;
- 不解压 FIT;
- 不生成 FIT 副本;
- 不删除文件;
- 不覆盖现有文件;
- 不做训练分析;
- 不参与平台查重。

## 运行

在项目根目录运行:

```bash
python tools/garmin_zip_renamer/garmin_zip_renamer.py
```

会打开一个简单 GUI:

1. 选择包含 Garmin ZIP 的目录;
2. 点击“扫描”;
3. 检查“原文件名 → 新文件名”预览;
4. 点击“执行重命名”;
5. 二次确认后才会真正改名。

扫描阶段不会修改任何文件。

## 命令行只读扫描

如果只想快速验证解析结果,可以运行:

```bash
python tools/garmin_zip_renamer/garmin_zip_renamer.py --scan .
```

该命令只打印预览,不会重命名。

## 文件名规则

默认格式:

```text
YYYY-MM-DD_HHMM_<activity-context>.zip
```

示例:

```text
2026-07-07_1924_treadmill-run.zip
2026-06-13_1718_road-run.zip
2026-06-17_1856_track-run.zip
```

说明:

- 时间来自 FIT 活动开始时间,不是 ZIP 文件修改时间;
- 默认按 Asia/Shanghai 时间命名;
- 同一天多次活动用 `HHMM` 区分;
- 如果目标名已存在,会自动加 `__2`, `__3` 后缀;
- 不会覆盖任何现有文件。

## 本地查重

工具扫描同一目录时,会根据 FIT 元数据识别重复活动。

当前重复判断键为:

```text
活动开始时间 + sport + sub_sport + 距离(四舍五入到米) + timer time(四舍五入到秒)
```

如果发现两个 ZIP 指向同一活动:

- 第一个文件按正常规则处理;
- 后续重复文件标记为 `DUPLICATE`;
- `DUPLICATE` 文件不会被重命名;
- 工具不会删除重复文件,需要用户自行确认后处理。

注意:这是本地文件夹内的视觉查重,不是平台查重,也不维护历史账本。换一个目录重新扫描,仍然只看该目录内现有文件。

## 当前运动类型映射

- running + sub_sport 0 → `road-run`
- running + sub_sport 1 → `treadmill-run`
- running + sub_sport 4 → `track-run`
- running + sub_sport 6 → `trail-run`
- cycling → `cycling`
- swimming → `swimming`
- hiking → `hiking`
- 其他未知类型 → `activity`

## 当前限制

- 第一版只处理 ZIP 内恰好一个 `.fit` 的 Garmin 导出包;
- ZIP 内没有 FIT 会标记错误;
- ZIP 内多个 FIT 会标记错误,需要人工处理;
- 只重命名 ZIP 本身,不解压;
- 还没有打包成 `.exe`,需要本机有 Python。

如果这个脚本版用起来顺手,后续可以再用 PyInstaller 打包成 Windows 可执行文件。