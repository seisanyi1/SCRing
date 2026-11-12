# SCRing / WellPrep

圆形图像提取、归一化和扇形切割工具，当前版本 **2.0.0**。

## 下载后能直接使用吗？

当前发布的是 **Python 源码版，不是独立 EXE**。首次使用需要安装 Python 和依赖；配置完成后，以后双击 `preprocess/start_ui.bat` 即可启动。

## Windows 首次安装与启动

1. 下载 [SCRing-2.0.0.zip](https://github.com/seisanyi1/SCRing/raw/refs/heads/main/dist/SCRing-2.0.0.zip)，右键选择“全部解压”。不要直接在压缩包里运行。
2. 安装 **Python 3.10 或以上**（本项目已在 Python 3.14 测试）。安装时勾选 **Add Python to PATH**；安装后重新打开终端。
3. 打开解压后的项目根文件夹，即同时包含 `README.md` 和 `preprocess` 文件夹的位置。在资源管理器地址栏输入 `powershell`，按回车。
4. 执行以下命令安装依赖（首次安装需要联网）：

   ```powershell
   python -m pip install -r preprocess/requirements.txt
   ```

5. 进入 `preprocess` 文件夹，双击 **`start_ui.bat`**。程序会启动本地服务，并自动打开浏览器操作界面。

如果浏览器没有自动打开，手动访问 <http://127.0.0.1:8768/>。

## 以后如何使用

直接双击 `preprocess/start_ui.bat`，无需重复安装依赖。使用期间保持启动命令窗口打开；关闭该窗口会停止服务。也可以在该窗口按 `Ctrl+C` 停止程序。

在界面中选择图片或文件夹，设置输出目录和参数，然后开始提取；提取完成后可进入圆域切割模块。

## 常见问题

- **提示找不到 Python，或输入 `python` 打开 Microsoft Store：** 检查是否已安装 Python 并勾选 Add Python to PATH，安装后重新打开终端。
- **提示 `No module named ...`：** 在项目根目录重新执行上面的依赖安装命令，确保安装依赖与启动程序使用同一个 Python。
- **双击启动后报错：** 保留窗口中的错误信息，也可以在项目根目录执行 `python preprocess/serve_ui.py --port 8768 --open` 查看输出。
- **8768 端口被占用：** 在项目根目录执行 `python preprocess/serve_ui.py --port 8769 --open`，然后访问 <http://127.0.0.1:8769/>。

服务默认仅监听本机 `127.0.0.1`，请在可信本地环境使用。如果需要无需安装 Python 的版本，需要另外制作 Windows 独立程序；本次源码包不包含此类程序。

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
