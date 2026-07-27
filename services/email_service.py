"""邮件外联服务"""
import smtplib
import html
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from config import settings


class EmailService:
    """邮件发送服务"""

    @staticmethod
    def send_email(to_email: str, subject: str, content: str, to_name: str = "") -> dict:
        """发送单封邮件"""
        if not settings.SMTP_HOST:
            return {"success": False, "error": "邮件服务未配置，请在 .env 中设置 SMTP 参数"}

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = settings.SMTP_USER
            msg["To"] = to_email

            # 如果知道客户名字，用名字称呼
            greeting = f"{to_name}，" if to_name else "您好，"
            escaped_content = html.escape(content).replace(chr(10), '<br>')
            html_content = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                <p>{greeting}</p>
                <div style="line-height: 1.8;">{escaped_content}</div>
                <hr style="margin-top: 30px; border: none; border-top: 1px solid #eee;">
                <p style="color: #999; font-size: 12px;">
                    此邮件由AI获客系统自动生成并发送。如需退订，请回复此邮件。
                </p>
            </div>
            """
            msg.attach(MIMEText(html_content, "html"))

            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
                server.starttls()
                server.login(settings.SMTP_USER, settings.SMTP_PASS)
                server.sendmail(settings.SMTP_USER, to_email, msg.as_string())

            return {"success": True, "to": to_email}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def batch_send(recipients: list, subject: str, content_template: str) -> list:
        """批量发送，使用模板"""
        results = []
        for r in recipients:
            email = r.get("email", "")
            name = r.get("name", "")
            if not email:
                continue
            # 简单模板替换
            personalized = content_template.replace("{name}", name)
            personalized = personalized.replace("{company}", r.get("company", ""))
            result = EmailService.send_email(email, subject, personalized, name)
            results.append({**r, **result})
        return results


email_service = EmailService()
