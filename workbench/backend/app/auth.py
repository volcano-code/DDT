from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from fastapi import Header
import hashlib,hmac,json,os
from .domain import DomainError

@dataclass(frozen=True)
class Principal:
    user_id:str
    tenant:str
    role:str
    accounts:tuple[str,...]
    def account(self,value:str):
        if value not in self.accounts: raise DomainError('forbidden','账户未授权。',403)
    def require(self,role:str):
        ranks={'viewer':0,'operator':1,'reviewer':2}
        if ranks.get(self.role,-1)<ranks[role]: raise DomainError('forbidden','角色无权执行此操作。',403)

class Auth:
    def __init__(self,entries:list[dict]):
        if not entries: raise RuntimeError('No users configured. Run scripts/init_local.py.')
        self.entries=entries
    def __call__(self,authorization:str|None=Header(default=None))->Principal:
        if not authorization or not authorization.startswith('Bearer '): raise DomainError('unauthorized','请输入本地初始化生成的访问令牌。',401)
        token=authorization[7:]
        hashed=hashlib.sha256(token.encode()).hexdigest()
        for entry in self.entries:
            if hmac.compare_digest(hashed,entry['token_sha256']):
                return Principal(entry['user_id'],entry['tenant'],entry['role'],tuple(entry['accounts']))
        raise DomainError('unauthorized','访问令牌无效。',401)

def load_users(path:Path)->list[dict]:
    if not path.is_file(): raise RuntimeError('Missing auth configuration. Run python scripts/init_local.py first.')
    entries=json.loads(path.read_text())
    for e in entries:
        if e.get('role') not in ('viewer','operator','reviewer') or not e.get('accounts') or len(e.get('token_sha256',''))!=64:
            raise RuntimeError('Invalid local user configuration.')
    return entries
