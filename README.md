# KAMUCL 头像与图标替换

把 [KAMUCL](https://github.com/kamubaba-i/KAMUCL) 启动器的头像和图标换成你自己的。

一条命令搞定四件事：**启动动画头像** + **吉祥物皮肤** + **程序图标** + **界面头像插件**。

作者：[@spirit1132](https://github.com/spirit1132) · 许可证：[MIT](LICENSE)

---

> ## ⚠️ 免责声明
>
> - 本项目是**第三方工具**，与 KAMUCL 官方及其作者**没有任何关系**，未经官方授权或认可。
> - 本工具通过修改 KAMUCL 的 `app.asar` 和可执行文件来实现资源替换。**修改后可能影响 KAMUCL 的自动更新、完整性校验或其它功能**，请自行评估风险。
> - 工具采用**「先验证再替换」**机制：补丁先写入临时文件、通过 Windows 加载器与范围校验后才覆盖原文件，失败时原文件不受影响。尽管如此，作者不对任何数据丢失或程序损坏负责。
> - 本工具**只替换图像资源**（头像 PNG、图标数据），不修改任何程序逻辑、不涉及账号数据、不联网。
> - KAMUCL 的名称、图标、默认头像等归其原作者所有。本工具仅在替换时读取/还原这些资源。
> - 请遵守 KAMUCL 项目的许可协议（原创部分为 MIT）与 Minecraft 最终用户许可协议（EULA）。
> - 本项目采用 **MIT 许可证**，详见 [LICENSE](LICENSE)。

---

## 快速开始（最推荐）

> 使用前：先把整个 `kamu_avatar_plugin` 文件夹拖进 KAMUCL 的
> `KAMUCL-runtime` 目录（和版本文件夹同级），详见下文「使用前」一节。

**双击 `一键美化.bat`** —— 自动完成全部四件事：

1. 启动动画头像（玻璃碎片汇聚出的那张）
2. 吉祥物皮肤（左上角头像点开的 3D 彩蛋角色）
3. 程序图标（任务栏 / 窗口 / 文件管理器）
4. 界面头像插件（主界面左上角，可随时开关）

脚本会自动：
- 检查 Python 和 Pillow
- 检测 KAMUCL 是否在运行，并询问是否自动关闭
- **自动从 KAMUCL 皮肤缓存导出你的账号皮肤**，无需手动准备图片
- 由皮肤自动生成配套图标
- 每步都做验证，失败自动回滚

### 想用自己的图片

```cmd
python install.py --skin "我的皮肤.png"
```

### 命令行选项

```cmd
python install.py --list               # 查看当前状态
python install.py --dry-run            # 试运行，不写文件
python install.py --skin "皮肤.png"     # 指定皮肤
python install.py --icon "图标.png"     # 图标单独指定
python install.py --no-icon            # 只改头像，不动图标
python install.py --no-splash          # 不换启动动画头像
python install.py --no-mascot          # 不换吉祥物皮肤
python install.py --no-plugin          # 不装插件
python install.py --kamu-dir "目录"     # 手动指定 KAMUCL 目录
```

> 所有补丁操作都要求 KAMUCL 已退出。脚本会检测并提示。

---

## 四个部件分别改了什么

| 部件 | 作用 | 影响范围 | 安全机制 |
|---|---|---|---|
| **插件 `avatar-swap`** | 主界面左上角头像 | 运行时生效，可随时开关 | 独立文件，删除即恢复 |
| **启动动画头像** | 启动时那张头像 | 改 app.asar | 先验证再替换 |
| **吉祥物皮肤** | 左上角头像点开的 3D 彩蛋 | 改 app.asar（同一次写入） | 先验证再替换 |
| **程序图标** | 任务栏/窗口/资源管理器 | 改 exe | 先验证再替换 |

> 为什么分三块：启动动画运行在**独立的 splash 窗口**、头像资源编译在 `app.asar` 内，
> 而 KAMUCL 插件运行在主窗口渲染进程、preload 只暴露 `invoke/on/send`，没有文件读写能力。
> 吉祥物彩蛋（Three.js 3D 角色）的皮肤同样编译在 `app.asar` 内，所以和启动动画一起由补丁工具处理。
> 程序图标则在 exe 的 PE 资源里，插件同样碰不到。所以后两者必须用补丁工具。

---

## 手动安装插件（如果不想用一键脚本）

1. 打开 KAMUCL → **设置** → **功能与插件** → **插件** → **打开插件目录**
   （目录一般是 `%APPDATA%\KAMUCL\plugins\`）
2. 新建文件夹 `avatar-swap`，把 `main.js` 和 `plugin.json` 复制进去
3. 回到插件列表点**刷新**，启用 `avatar-swap`
4. 重启 KAMUCL

```
%APPDATA%\KAMUCL\plugins\avatar-swap\
├─ plugin.json
└─ main.js
```

### 用法

主界面左上角头像右侧会出现一个 **◧** 小按钮，点开设置面板：

- **跟随账号皮肤** —— 读取当前账号皮肤，自动裁出头部（含帽子/头发叠加层）
- **导入 PNG** —— 选本地图片。是皮肤尺寸（64×32 / 64×64 / 高清）就裁头部，其他尺寸整图使用
- **刷新** —— 重新拉取
- **恢复默认** —— 还原成原来的卡慕头像

选择会记在 `localStorage`，下次启动自动应用。

> 离线账号需要有离线皮肤才能"跟随"；正版账号直接读当前皮肤。

---

## 单独使用：启动动画头像 / 吉祥物皮肤

### 前置

```cmd
pip install Pillow
```

### 快速开始（Windows CMD，可直接复制）

把皮肤文件放到本工具目录下，然后在**本目录**执行（`--skin` 后面填你真实的皮肤文件名）：

```cmd
python patch_splash_avatar.py --skin my_skin.png
```

> 提示：`--skin 你的皮肤.png` 里，后半段是**你要替换的文件名**，不是照抄的占位符。
> 皮肤文件不存在时会提示 `皮肤文件不存在: xxx.png`。

如果皮肤在别处，写完整路径（路径含空格要用双引号包住）：

```cmd
python patch_splash_avatar.py --skin "D:\我的文件\皮肤\skin.png"
```

指定 app.asar（不写会从脚本所在位置向上找）：

```cmd
python patch_splash_avatar.py --asar "C:\Users\你\Desktop\KAMUCL-runtime\1.1.21-09cc092a80627466\resources\app.asar" --skin my_skin.png
```

这个脚本**默认同时替换两处**（都在 app.asar 里，合并为一次写入）：

| 目标 | 说明 | 贴图规格 |
|---|---|---|
| 启动动画头像 | 启动时玻璃碎片汇聚出的那张脸 | 256×256（由皮肤头部裁出） |
| 吉祥物皮肤 | 左上角头像点开的「卡慕互动彩蛋」3D 角色 | 64×64 全身皮肤 |

只换其中一个：

```cmd
python patch_splash_avatar.py --no-mascot --skin my_skin.png      # 只换启动动画头像
python patch_splash_avatar.py --only-mascot --skin my_skin.png    # 只换吉祥物皮肤
```

> 吉祥物要求 64×64 现代格式皮肤；64×32 旧版皮肤会先按 KAMUCL 自己的算法
> 转换成 64×64（镜像补全四肢背面、清理帽子叠加层），转换逻辑与原版一致。
> 模块里另外两个 15×15 的手掌印反馈小图（palm/print）保持原样不动。

#### slim / classic 双模型适配

skins 有 **classic（4 宽手臂）** 和 **slim（3 宽手臂）** 两种布局，两者的手臂贴图
区域宽度不同，不能混用。

KAMUCL 的吉祥物模块**自己从不设置模型类型**（`SkinObject.slim` 默认 `false`，
构造末尾固定执行 `this.modelType = "default"`），而它的皮肤预览页
（`index-*.js`）却是按 `detectSkinVariant()` 动态设置的——**吉祥物这里漏了那一步**。
所以工具会把类型显式写进模块，slim 和 classic **两种都写**：

```js
player = new PreviewPlayer();
player.skin.map = skin;
player.skin.modelType = "slim";     // slim 皮肤
player.skin.modelType = "default";  // classic 皮肤
```

`set modelType(v)` 内部是 `this.slim = (v === "slim")` 再跑一遍
`modelListeners`，同步手臂 mesh 的 `scale.x`（4 ↔ 3）、UV 宽度和 pivot 偏移。
所以：

- **slim**：不写就退化成 classic，手臂多采 x=47 那一列（相邻面），表现为竖向色带
- **classic**：显式写 `"default"` 才能正确覆盖之前打过的 slim，不依赖"精确删行"
- 两种写法都**幂等**，可以 slim ⇄ classic 反复切换，模块里始终只有一条声明

类型判定用 KAMUCL 的 `detectSkinVariant` 同款算法：取 `(54,20)` 起 1×12 的
一整列 alpha，有彩色即 classic、全透明即 slim。若皮肤来自 KAMUCL 缓存，则优先
采用缓存里的 `variant` 字段（更可靠）。用 `--list` 可以核对两者是否一致：

```
吉祥物皮肤：553 字节，64x64
  └ 皮肤模型：贴图=slim | 模块声明=slim
```

不一致时会直接标出来：

```
  └ 皮肤模型：贴图=slim | 模块声明=classic  ← 不一致，手臂会错位
```

`--skin` 指定任意图片时，非皮肤尺寸会整图缩放为 64×64 并按探测结果处理。

#### 回归测试

```cmd
python test_mascot_variant.py
python test_mascot_variant.py --asar "KAMUCL-runtime\<版本>\resources\app.asar"
```

第二种会在真实 asar 的副本上跑完整流程（40 项断言），覆盖探测算法、双向切换、
幂等、`node --check` 语法、以及「只改动目标模块」。

### 查看当前状态

```cmd
python patch_splash_avatar.py --list
```

### 试运行（不写文件）

```cmd
python patch_splash_avatar.py --skin my_skin.png --dry-run
```

---

## 单独使用：程序图标

替换任务栏、窗口左上角、文件管理器里显示的那个图标。

### 会改哪些文件

| 文件 | 说明 |
|---|---|
| `KAMUCL-runtime\<版本>\KAMUCL.exe` | 启动器本体（任务栏/窗口图标） |
| `KAMUCL-<版本>.exe` | 便携安装器（文件管理器里看到的图标） |

两个文件各内嵌 7 种尺寸：16 / 24 / 32 / 48 / 64 / 128 / 256。

### 用法

```cmd
python patch_icons.py --list
```

```cmd
python patch_icons.py --icon my_icon.png
```

只改其中一个：

```cmd
python patch_icons.py --icon my_icon.png --target runtime
python patch_icons.py --icon my_icon.png --target installer
```

恢复：

```cmd
```

试运行：

```cmd
python patch_icons.py --icon my_icon.png --dry-run
```

### 图标建议

- **正方形**，256×256 或更大（非正方形会自动居中裁剪）
- 像素风图片（如 Minecraft 头像）效果最好，体积也最小
- 抗锯齿的复杂图片也可以，工具会自动量化压缩以适应空间限制
- 如果图标太复杂放不下，工具会自动去掉 256×256 再试

### 空间限制

两个文件的 `.rsrc` 节剩余空间不同，工具会自动按**最紧的那个**来压缩：

| 文件 | 节容量 | 原图标占用 | 可用预算 |
|---|---|---|---|
| `KAMUCL.exe` | 99840 | 13273 | 37294 字节 |
| `KAMUCL-1.1.21.exe` | 16384 | 13273 | 13505 字节 |

所以图标总大小要控制在 **约 13 KB** 以内（工具会自动尝试压缩并提示）。

---

## 打补丁前请先关闭 KAMUCL

补丁会替换被占用的文件，**必须先退出启动器**。用这条命令确认：

```cmd
tasklist | findstr /i KAMUCL
```

没有输出就表示已关闭。

> 如果忘了关，会看到 `PermissionError: [WinError 5] 拒绝访问`。
> 关闭 KAMUCL 后重新执行即可。即使补丁失败，原文件也不会被改动（见实现说明）。

---

## 实现说明

### 插件侧

- 头像元素是主界面里的 `img.brand-head`（42×42，`image-rendering: pixelated`）
- 皮肤 → 头像的裁切规则（以 64×64 皮肤为基准，高清按比例放大）：
  - 头部正面：`(8,8)` 起 8×8
  - 帽子/头发叠加层：`(40,8)` 起 8×8，用 alpha 合成
  - 64×32 旧版皮肤没有帽子层，自动跳过
- 因为 Vue 重渲染会把 `src` 还原，插件每 500ms 把替换后的头像贴回去
- 小按钮跟随头像位置，窗口 resize 时自动重排

### 启动动画补丁 / 吉祥物补丁

两者都在 app.asar 内，机制相同：模块里以 base64 内嵌 PNG，
工具按原有 offset 顺序重写所有文件、重算 offset/size，并重算目标文件的完整性字段。

asar 文件结构（Chromium Pickle）：

```
[u32 4][u32 headerBufLen][u32 payloadSize][u32 jsonLen][JSON header][pad4][文件数据紧密排列]
```

- `data_offset = 8 + headerBufLen`
- 文件数据区**无对齐填充**，紧密排列
- 每个文件的 `integrity` = `sha256(文件内容)`

| 目标 | asar 内路径 | 内嵌内容 |
|---|---|---|
| 启动动画头像 | `out/renderer/assets/splash-face-*.js` | `faceUrl`，256×256 头部 PNG |
| 吉祥物皮肤 | `out/renderer/assets/MascotStage-*.js` | `skinUrl`，64×64 全身皮肤 PNG |

吉祥物模块里还有 `palmUrl` / `printUrl` 两个 15×15 反馈小图，正则只匹配
`skinUrl`，它们原样保留。两个目标同属一个 asar，工具合并成**一次写入**，
避免重复重写 18.9MB 文件。

**只有目标模块的内容被改动，其余 380 个文件逐字节保持不变。**

### 图标补丁

图标存在 PE 的 `.rsrc` 资源节里，结构是三层目录树：

```
RT_GROUP_ICON(14) ─┬─ ID=1 ─┬─ lang=1033 ─→ 数据条目
RT_ICON(3) ────────┼─ ID=1 ─┬─ lang=1033 ─→ 数据条目（16x16 图像）
                   ├─ ID=2 ─┴─ ...        （24x24）
                   └─ ...
```

工具采用的策略是**完整保留原目录树和数据条目表**，只把数据区搬到条目表之后，
再更新每个条目的 RVA 与长度。这样目录结构（计数、偏移、命名资源、语言）完全不受影响，
是风险最低的做法。同时：

- 复用原有的图标 ID 序列和语言 ID（安装器的槽位顺序是 `[7,5,6,3,4,2,1]`，非升序）
- 保持节长度不变（原地覆盖），后续节偏移不移动
- **`.rsrc` 节的 `vsize` 保持原值不变**（见下方"踩坑记录"）
- 原文件 CheckSum 为 0（electron-builder 常态，表示不校验），保持 0 不变

#### 踩坑记录：为什么不能缩小 `.rsrc` 的 vsize

第一版工具在重建资源后，把节头的 `VirtualSize` 更新成了新数据的实际大小
（`0x1282b` → `0x10b89`），并且同步改了数据目录里的资源大小。
结果 **Windows 直接拒绝加载**：

```
CreateProcessW -> 失败，GetLastError = 193 (ERROR_BAD_EXE_FORMAT)
```

现象是双击启动器毫无反应、不产生任何日志。原因是 Windows 映像加载器会校验
节表与 `SizeOfImage` 的一致性，缩小已存在节的 `vsize` 会让映像布局判定为非法。

修正方式：**只覆盖节内容，不动节头的 `vsize`，也不动数据目录大小**。
资源数据放在节内（`rsize` 范围内）即可被正常读取。

修复后的验证：

```
CreateProcessW:
  原始 KAMUCL.exe        -> 成功
  缩小 vsize 的版本      -> 失败 (193)
  修正后的版本           -> 成功

实际启动 KAMUCL.exe 日志:
  Startup: assets complete
  Startup: paint complete
  Startup: renderer resources settled
  Startup: main window revealed after renderer + compositor + avatar ready
```

### 已验证

启动动画头像 / 吉祥物皮肤补丁：

- 完整性哈希重算正确（读回校验通过）
- 补丁后模块仍是合法 JS（`const faceUrl = "..."` / `const skinUrl = "..."` + 其余代码原样）
- 真实 18.9MB app.asar 上替换后，其余 380 个文件逐字节未变
- 吉祥物模块里的 `palmUrl` / `printUrl` 反馈小图逐字节未变
- 64×32 旧版皮肤转换路径实测：四肢背面镜像正确、全不透明皮肤的帽子层被清空
- 用 KAMUCL 自带的 Electron 加载补丁后 asar，启动动画完整跑完（`ready` → 状态推送 → `assembled`）
- 从补丁后的 asar 提取头像，确认裁头与帽子层叠加正确

图标补丁：

- **`CreateProcessW` 能成功创建进程**（这是最硬的判据，`ERROR_BAD_EXE_FORMAT` 会在这里暴露）
- **实际启动 KAMUCL.exe 跑通完整启动流程**（日志到 `main window revealed`）
- **Windows API（`ExtractIconEx`）能正常提取新图标**，返回码与原始文件一致
- 全文件逐字节比对：差异**只出现在 `.rsrc` 节内**，节外 0 字节改动
- 所有节头逐字节相同、`SizeOfImage` 不变、文件大小不变
- 目录区逐字节相同、条目键与偏移完全一致、所有 RVA 合法、数据区无重叠
- 非图标资源（光标、版本信息、清单等）逐字节保持不变

---

## 安全机制：先验证再替换

本工具**不生成任何备份文件**。取而代之的是一套「先验证再替换」流程：

```
① 构建补丁结果，写入同目录临时文件（<文件名>.kamucl-tmp）
② 校验：
   · exe 补丁 —— 结构性校验：逐字节比对补丁前后，确认只改动了 .rsrc 节，
     PE 头部与代码段等其它区域零改动
   · asar 补丁 —— 完整性校验：重建后读回目标模块，比对 SHA256 与
     头部 integrity 字段，确认数据可正确寻址
③ 加载器校验（exe）：用 CREATE_SUSPENDED 创建进程（挂起状态，不执行任何代码），
   验证 Windows 能真正映射该映像
④ 全部通过才 os.replace() 覆盖原文件（NTFS 上是原子操作）
⑤ 任一步失败：删除临时文件，原文件从未被修改
```

替换多个文件时采用**两阶段提交**：所有目标都通过 ①-③ 后才统一执行 ④，
避免"第一个成功、第二个失败"的中间状态。

恢复方法：本工具不做备份，因此打完补丁后若发现异常，
**重新运行 KAMUCL 的安装器即可完全恢复原状**（安装器会从内嵌的
app-64.7z 重新解压所有文件）。

---

## 注意事项

- **出问题如何恢复**：本工具不做备份（采用「先验证再替换」，失败时原文件不受影响），
  因此打完补丁后如果发现异常，**重新运行 KAMUCL 的安装器即可完全恢复原状**
  （安装器会从内嵌的 app-64.7z 重新解压）。
- **升级 KAMUCL 后补丁会丢失**（新版本会解压出新的 asar 和 exe），需要重新打一次。
  插件不受影响，仍在 `%APPDATA%\KAMUCL\plugins\` 里。
- 补丁前请**关闭 KAMUCL**，否则文件被占用无法替换。
- 工具只改头像/图标资源，不碰任何代码逻辑。

---

## 文件清单

```
kamu_avatar_plugin\
├─ 一键美化.bat                 双击运行：头像 + 吉祥物 + 图标 + 插件
├─ install.py                   一键脚本主体
├─ plugin.json                  插件清单
├─ main.js                      插件主体（主界面头像替换）
├─ patch_splash_avatar.py       启动动画头像 + 吉祥物皮肤补丁工具
├─ patch_icons.py               程序图标补丁工具
├─ safe_replace.py              安全替换（先验证再替换，无备份）
├─ env_check.py                 环境依赖检查与自动安装
├─ test_mascot_variant.py       slim / classic 模型类型回归测试
├─ LICENSE                      MIT 许可证
├─ .gitignore
└─ README.md                    本文件
```

### 发布/分发时的最小文件集

只发两个 `.bat` **不能用**（它们只是入口，实际执行的是 `install.py`）。
必须一起打包这 8 个文件（约 110 KB），且**必须放在同一目录**：

```
一键美化.bat
install.py
env_check.py
safe_replace.py
patch_icons.py
patch_splash_avatar.py
main.js
plugin.json
```

> `test_mascot_variant.py` 只是开发期回归测试，分发时可以不带上。

### 运行环境要求

- **Windows**（脚本调用 Win32 API 与 `tasklist`/`taskkill`，不支持 Linux/macOS）
- **Python 3.6 或更高**
- **Pillow**（图像处理）——**缺失时会自动安装**

### 环境检查与自动安装

所有依赖的检查和安装集中在一个脚本 [env_check.py](env_check.py) 里，
`install.py`、`patch_icons.py`、`patch_splash_avatar.py` 和
`一键美化.bat` 都会先过它一遍。

单独运行：

```cmd
python env_check.py              # 检查并（询问后）安装
python env_check.py --list       # 只看状态
python env_check.py --yes        # 不问，直接装
python env_check.py --no-install # 只检查，不安装
```

`--list` 的输出：

```
  ✓ Python 3.13.2
    解释器：D:\python\python.exe
    虚拟环境：否

  ✓ Pillow 12.3.0（必需，图像处理（皮肤裁切、图标生成））

  ✓ pip 可用
```

自动安装会按顺序尝试，直到成功：

1. 默认源 `python -m pip install --upgrade Pillow`
2. `--user` 模式（应对全局站点包无写权限）
3. 清华镜像、阿里云镜像（应对默认源超时）

若连 pip 都没有（精简版 Python、部分 venv），会先跑
`python -m ensurepip --upgrade` 把 pip 修好再装，**不需要联网下载 get-pip**。

安装完会再复查一次 `import`，防止 pip 报告成功但实际装到了别的解释器。
全部失败时给出可直接复制执行的命令，包括：

- 三条安装命令（默认 / `--user` / 镜像）
- `ensurepip` 修复命令
- 设置全局镜像源的命令（国内网络慢时）

**控制环境行为的开关：**

| 方式 | 作用 |
|---|---|
| `--no-install-deps`（install.py） | 缺依赖时不自动安装，只提示 |
| `--yes` / `-y` | 自动安装时不询问 |
| 环境变量 `KAMUCL_NO_INSTALL=1` | 任何脚本都不安装，只提示 |
| 环境变量 `KAMUCL_YES=1` | 任何脚本都不询问，直接装 |

> 兼容性说明：勾号/叉号符号在 GBK 控制台（`chcp 936`）下会触发
> `UnicodeEncodeError`，所以 env_check 会先把 stdout 切到 UTF-8，
> 切不动就自动退化成 ASCII 的 `v` / `x`，保证任何代码页都不会崩。

### 使用前：把本文件夹放进 KAMUCL-runtime

脚本不做全盘扫描，只从自己所在的位置就近定位。使用前，把整个
`kamu_avatar_plugin` 文件夹拖进 KAMUCL 的 `KAMUCL-runtime` 目录，
和版本文件夹放在一起：

```
KAMUCL-runtime\
├── 1.1.21-xxxxxxxx\        ← KAMUCL 的版本文件夹（不用动）
│   ├── KAMUCL.exe
│   └── resources\app.asar
└── kamu_avatar_plugin\     ← 把整个文件夹拖到这里
    ├── 一键美化.bat
    └── install.py
```

放好之后双击 `一键美化.bat` 即可。脚本只向上找两级，找不到会明确
提示，不会误认别的目录。也可以用 `--kamu-dir` 手动指定：

```cmd
python install.py --kamu-dir "C:\Users\你\Desktop\KAMUCL-runtime"
```

