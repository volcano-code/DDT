#!/usr/bin/env python3
"""Create local-only synthetic demo credentials; never reuse a bundled password."""
import hashlib,json,secrets,os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
state=Path(os.environ.get('DDT_STATE_DIR',ROOT/'state'))
state.mkdir(parents=True,exist_ok=True);state.chmod(0o700)
users_file=state/'users.json'
if users_file.exists():
    print(f'保留已有用户配置：{users_file}。令牌首次生成后只保存在本地 state/access-tokens.txt。')
else:
    users=[];tokens=[]
    for role in ['reviewer','operator','viewer']:
        token=secrets.token_urlsafe(32)
        users.append({'user_id':'local-'+role,'tenant':'DEMO','role':role,'accounts':['TEST-001'], 'token_sha256':hashlib.sha256(token.encode()).hexdigest()})
        tokens.append(f'{role}: {token}')
    users_file.write_text(json.dumps(users,indent=2));users_file.chmod(0o600)
    path=state/'access-tokens.txt';path.write_text('\n'.join(tokens)+'\n');path.chmod(0o600)
    print('已初始化 DEMO / TEST-001 演示身份。访问令牌仅保存在：'+str(path))
print('启动后在浏览器输入 reviewer 令牌。不要将 state/ 目录提交、分享或上传。')
