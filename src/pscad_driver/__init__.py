# PSCAD 4.6.2 × AI 自动化驱动
#
# 设计原则
#   1. 只读原始资产：PSCAD 安装目录、模板工程一律不改；所有实验在 cases/ 副本上进行。
#   2. 完成判据看产物：build/run 都是异步提交，必须等 exe / .inf / _0N.out 落地。
#   3. 物理量只来自文件：读数只解析 .inf + .out，不做任何推断。
#   4. 几何自算：4.6.2 无端口坐标查询接口，端口坐标由库内端口相对坐标 + 实例左上角推算。

from .config import load_config, CONFIG          # noqa: F401
from .transport import Transport                # noqa: F401
from .runner import Runner                      # noqa: F401
from .reader import read_out, read_inf, to_csv   # noqa: F401

__version__ = "0.1.0"
