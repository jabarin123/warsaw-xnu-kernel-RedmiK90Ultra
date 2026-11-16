# v102：v95b 以来的 SpringBoard SIGSEGV 回归已修复（2026-11-16）

## 结论

**根因是 v94/v95 重构时把 `/etc/rc` 里的 Metal 插件注册行删掉了**：

```
/redwood/bin/metal_props on ../../../redwood/lib/RedwoodMetal RedwoodMetalDevice
```

恢复这一行（v102，通过 `rc.launchd` 模板重建 root26.apfs）之后：

| 指标 | v95b–v101（无注册） | **v102（恢复注册）** |
|---|---|---|
| SpringBoard SIGSEGV | 每轮 1–2 次 | **0** |
| SpringBoard 重启 | 有（finished startup 2 次） | 1 次，无重启 |
| `redwoodmetal[SpringBoard]` 渲染日志 | 缺失 | 正常（Siri 管线等） |
| `Weak reference loaded` 警告 | v97/v98 各 1–2 条 | 0 |
| 520 s 观察窗结束时 | 桌面→崩溃→黑屏转圈 | **UI 存活**（App 资源库页，图标/状态栏正常） |

## 证据链（一次只改一个变量）

| 轮 | 变量 | 结果 |
|---|---|---|
| v93 / v93restore | 有注册行（旧 2117 B `/etc/rc`） | 0 崩溃 |
| v94 | 只换 RedwoodMetal bundle + libredwoodmetal（GPU 路径） | 2× SIGTRAP（另一回事，旧栈） |
| v95 | `/etc/rc` 重构（**删掉 metal_props**）+ 新 AIR | SIGSEGV 首现于 v95b，此后每轮 1–2 次 |
| v96–v98 | 只改 sb26/glass（cold=80 / glass off） | 崩溃照旧 → glass 开关与冷等待不是根因 |
| v99 | cold=120 | 桌面 55 s→崩→黑屏；120 ms 不背锅 |
| v100 | **原地恢复 metal_props 行**（rc.jobs 内联编辑） | **0 崩溃**，但 480 s 内未见桌面（原地编辑轮，观察窗不够长） |
| v101 | 回退注册、加脚本化白条上滑 | 崩溃回归（2 次）→ 排除手势因素，崩溃是自主发生的 |
| **v102** | **从模板重建**：rc.launchd 加回注册行 + glass 默认 cold=80 | **0 崩溃，UI 全程存活** |

v100 的"没见到桌面"是长窗口取证不足 + 原地 APFS 编辑轮的不确定性；v102 用干净重建 + 520 s
观察窗把两个问题都关掉了。

## v102 产物

- `payload-v102.bin` sha256 `1023221cd622e008e29e47423d4c7666a51f806d037e4e6c09c963ab7de64e93`
  （35,764,752 B；内核/cmdline 与 v99 逐字节相同，只换 ramdisk）
- ramdisk `root26.apfs` sha256 `d05f98bfd5cac96ec3af4c8d0b95901f459b58a7de03f30d7d7e90859fd33104`
- glass.conf：`on=1`，`xpc_cold_ms=80`（v97 已验证值；120 ms 未证明有收益，不采用）
- 取证：`build-out/warsaw-20261107-xnu/once-v102/`（OBSERVE=520， snaps @55–470 s）

## 改动

- `userland/rc.launchd`：在 `exec /redwood/sbin/launchd.ios` 之前恢复注册行，并注释说明
  为什么这行不能丢（v102 起）。
- 构建宿主注意：本机 Git Bash 没有 `shasum`，build.sh 的 metal-stamp 校验会误报
  "dylib 不是当前源码编的"。`build-out/pybin/shasum`（python shim，输出格式同 perl shasum）
  已补上，构建时把 `build-out/pybin` 前置到 PATH 即可（PIL 也靠它）。

## 复现命令

```bash
cd /f/warsaw-ios26/tree/userland
PATH="/c/Users/Administrator/WorkBuddy/2026-11-06-17-23-24/build-out/pybin:$PATH" \
  PREBUILT=1 IOS=../ios26 MSYS=winsymlinks:nativestrict ./build.sh

cd /c/Users/Administrator/WorkBuddy/2026-11-06-17-23-24
build-out/pybin/python3 build-out/mkpayload-v102.py

cd warsaw-port/tools
OUTDIR=.../once-v102 OBSERVE=520 SHOTS="55 80 120 160 200 260 320 400 470" \
  ./boot-once-v2.sh <Mu-warsaw-xnu-k10.img> <payload-v102.bin> F:/warsaw-ios26/tree/userland/system26.apfs
```

## 仍未解决（与本次回归无关，维持原优先级）

1. 点图标闪红 + 启动动画掉帧（帧空窗 50–109 ms、iconservices 冷超时）。
2. 图标 / Dock / widget 半透明玻璃（A+C）：必须走 iconservicesagent GPU 原生渲染；
   v94 证明能跑但缺 mip 级视图与附件清屏掩码。
3. widget 白底块（`0016` 已在源码树，等待编进 AIR 库）。
