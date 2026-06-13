"""统一异常与错误码。

约定（架构文档 02 第 5 节）：所有接口返回统一包装
    { "code": 0, "message": "ok", "data": ... }
- code = 0 表示成功；非 0 表示业务错误（前端据此弹提示）。
- HTTP 状态码仍然语义化使用（404/422/500），但前端主要看 code 字段。
"""

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


class BizError(Exception):
    """业务异常：服务层主动抛出，会被全局处理器转成统一响应包。

    例：raise BizError(40401, "股票代码不存在")
    """

    def __init__(self, code: int, message: str, http_status: int = 400):
        self.code = code
        self.message = message
        self.http_status = http_status
        super().__init__(message)


def ok(data: Any = None) -> dict:
    """成功响应的统一包装，路由层直接 `return ok(结果)`。"""
    return {"code": 0, "message": "ok", "data": data}


async def biz_error_handler(_request: Request, exc: BizError) -> JSONResponse:
    """全局捕获 BizError，转成统一 JSON 响应。在 main.py 注册。"""
    return JSONResponse(
        status_code=exc.http_status,
        content={"code": exc.code, "message": exc.message, "data": None},
    )


async def unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """兜底处理器：未预料的异常统一返回 500，避免泄露堆栈细节给前端。"""
    return JSONResponse(
        status_code=500,
        content={"code": 50000, "message": f"服务器内部错误: {exc}", "data": None},
    )
