"""Safe failure reasons and verification of one already accepted lightweight turn."""

FAILURE_MESSAGES = {
    'quota_exhausted': '此账号当前额度已用尽，本次未完成；不会自动补发',
    'turn_interrupted': '本轮执行已中断，未完成；不会自动补发',
    'turn_failed': '本轮执行失败，未完成；不会自动补发',
    'observation_timeout': '等待本轮完成超时，结果未确认；不会自动补发',
    'observation_disconnected': '执行观察连接已断开，结果未确认；不会自动补发',
    'verification_failed': '本轮完成记录或消息未核验通过，结果未确认；不会自动补发',
    'preparation_failed': '账号或会话准备失败，本次未发送；请检查账号登录及后台连接',
    'submission_uncertain': '提交响应未确认，结果未知；不会自动补发',
}


class SendMissed(RuntimeError):
    """The send has not begun, and its preparation window has expired."""


class TurnError(RuntimeError):
    def __init__(self, code, turn_id):
        self.code = code
        self.turn_id = turn_id
        super().__init__(FAILURE_MESSAGES[code])


def terminal_error(turn):
    """Never expose native error text, which may contain private upstream details."""
    if (turn.get('error') or {}).get('codexErrorInfo') == 'usageLimitExceeded':
        return 'quota_exhausted'
    if turn.get('status') == 'interrupted':
        return 'turn_interrupted'
    if turn.get('status') == 'failed':
        return 'turn_failed'
    return None


def read_turn(rpc, thread_id, turn_id):
    """Match the exact thread and turn; a previous successful turn proves nothing."""
    data = rpc.call('thread/read', {'threadId': thread_id, 'includeTurns': True})['thread']
    if data.get('id') != thread_id:
        raise TurnError('verification_failed', turn_id)
    turn = next((t for t in data.get('turns', []) if t.get('id') == turn_id), None)
    if turn:
        code = terminal_error(turn)
        if code:
            raise TurnError(code, turn_id)
    return data, turn


def completed_messages(turn):
    if not turn or turn.get('status') != 'completed':
        return None
    items = turn.get('items', [])
    has_user = any(item.get('type') == 'userMessage' and len(item.get('content', [])) == 1
                   and item['content'][0].get('type') == 'text'
                   and item['content'][0].get('text') == 'ok' for item in items)
    finals = [item for item in items if item.get('type') == 'agentMessage'
              and item.get('phase') != 'commentary']
    return finals if has_user and finals else None
