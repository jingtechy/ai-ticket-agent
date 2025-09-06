from sqlalchemy import Column, Integer, String, DateTime, Text
from sqlalchemy.ext.declarative import declarative_base
import datetime

Base = declarative_base()

class TicketLog(Base):
    __tablename__ = "ticket_logs"
    id = Column(Integer, primary_key=True)
    slack_user = Column(String)
    slack_channel = Column(String)
    ticket_id = Column(String)
    jira_issue_key = Column(String)
    llm_result = Column(Text)
    status = Column(String)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
