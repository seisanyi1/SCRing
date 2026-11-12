# SCRing / WellPrep

圆形图像提取、归一化和扇形切割工具，当前版本 **2.0.0**。

## 快速启动

需要 Python 3.10 或以上（已在 Python 3.14 测试）。从仓库根目录执行：

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r preprocess\requirements.txt
.venv\Scripts\python preprocess\serve_ui.py --port 8768 --open
```

浏览器地址：http://127.0.0.1:8768/ 。仅供可信本地环境使用。

[完整说明](preprocess/README.md) · [版本记录](preprocess/CHANGELOG.md)

## 源码封包

下载 [SCRing-2.0.0.zip](dist/SCRing-2.0.0.zip)，解压后按上述步骤安装依赖并启动。
此包包含源码、网页资源、测试、样本和验证资料；需要安装 Python，不是独立 EXE。
`dist/SHA256SUMS.txt` 提供校验值。运行历史初始为空。

重新打包：`python tools/package_release.py`。

## 测试

```powershell
cd preprocess
python -m unittest discover -s tests -v
```

17 项测试覆盖圆拟合、图像输出、切割和本地界面后端。
