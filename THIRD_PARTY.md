# 随包依赖

`vendor/` 内包含 MIDI 导入所需的纯 Python 依赖，无需联网安装。

| 名称 | 版本 | 许可 | 随包许可文件 |
| --- | --- | --- | --- |
| Mido | 1.3.3 | MIT | `vendor/mido-1.3.3.dist-info/LICENSE` |
| packaging | 26.3 | Apache-2.0 或 BSD-2-Clause | `vendor/packaging-26.3.dist-info/licenses/LICENSE`、`LICENSE.APACHE`、`LICENSE.BSD` |
| ViGEmClient | DLL 1.0.0.1（vgamepad 0.1.0 分发） | MIT | `assets/gamepad/ViGEmClient-LICENSE.txt` |

完整许可与版权声明保留在上述文件中。Mido 用于读取 MIDI；packaging 是其版本处理依赖。本程序的 MIDI 读取不使用 MIDI 设备后端。

ViGEmClient 用于可选的虚拟 Xbox 输入，包括右摇杆扫弦、左摇杆选择圆盘、拨弦按钮与 LB／RB 翻页。客户端来源为 [vgamepad 0.1.0 官方发布包](https://pypi.org/project/vgamepad/0.1.0/)，上游为 [nefarius/ViGEmClient](https://github.com/nefarius/ViGEmClient)。程序未运行 vgamepad 的安装脚本，也不包含其驱动安装器。使用虚拟手柄输入需另行安装 [ViGEmBus v1.22.0](https://github.com/nefarius/ViGEmBus/releases/tag/v1.22.0)。ViGEm 项目已停止维护，此功能作为可选方式保留，键盘与鼠标输入不依赖该驱动。

Windows EXE 使用 PyInstaller 打包，包含 Python 3.13 及 Tcl/Tk 运行时。便携包的 `licenses/` 保留 Python、Tk 和 PyInstaller 的许可；PyInstaller 引导程序适用其分发例外条款。构建工具本身不需要由用户安装。

明音凛角色与立绘属于《异环》及其权利方。程序头像使用立绘参考的改编图，来源和制作说明见 [assets/icon-source.md](assets/icon-source.md)。

`examples/小星星_导入演示.mid` 是根据本程序已有的短示范旋律自行生成的演示文件。
