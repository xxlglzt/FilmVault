"""开发入口：python run.py 后浏览器打开 http://127.0.0.1:8800"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8800, reload=False)
