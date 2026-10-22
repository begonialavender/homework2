import os


class Config:
    """所有配置都可以用环境变量覆盖，方便换机器与第二阶段部署。"""

    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-secret-please-change")

    MONGO_URI = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017")
    MONGO_DB = os.environ.get("MONGO_DB", "survey_system")

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    JSON_AS_ASCII = False

    # 允许的题型与跳转目标常量在 app/logic.py 中维护
    MAX_QUESTIONS_PER_SURVEY = int(os.environ.get("MAX_QUESTIONS_PER_SURVEY", "50"))
