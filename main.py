"""本地启动入口；字段抽取逻辑位于app目录。"""
import uvicorn

# 默认仅监听本机；部署时可通过uvicorn命令另行指定地址和端口。
if __name__ == '__main__':
    uvicorn.run('app.api:app', host='127.0.0.1', port=8000)
