---
name: send-digest
description: docs/pending.json의 신규 항목을 이메일로 발송할 때 사용
---
# 절차
1. docs/pending.json을 읽는다. 0개면 발송하지 않고 종료한다.
2. config/email.yml의 recipient, subject_prefix를 읽는다.
3. 각 항목을 [제목·핵심 요약·URL]로 정리해 본문을 만든다.
4. resend MCP로 발송한다.
5. 발송 성공을 확인받은 뒤에만 mark_sent.py 실행을 제안한다.
