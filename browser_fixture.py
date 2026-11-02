"""Fictional data for browser checks and README screenshots; never opens D:/ data."""
import json
from pathlib import Path
import sys
import tempfile
import threading

from gui import create_server
from store import MemoryStore


def main():
    with tempfile.TemporaryDirectory(prefix='codex-memory-browser-',ignore_cleanup_errors=True) as directory:
        store=MemoryStore(directory)
        samples=[
            ('界面设计约定','preference','','界面要安静、清楚、好操作。\n\n优先使用系统字体，保持克制的配色和一致的按钮样式。窗口打开要有即时反馈，常用操作不要等待动画。\n\n重要的决定保留依据，方便以后确认为什么这样做。',3),
            ('文档与代码放在一起','workflow','','项目说明随代码一起更新，首页先写怎么使用，再写实现方式。',2),
            ('先保留来源，再写结论','decision',str(Path(directory)/'示例笔记'),'正式记忆保存可复用的结论，并保留必要的短片段作为依据。',3),
            ('下一阶段的工作','state',str(Path(directory)/'示例笔记'),'先完成搜索与编辑体验，再检查窄窗口布局。',2),
            ('备份方式','workflow',str(Path(directory)/'生活看板'),'备份完整数据目录；索引可以重建，正式笔记和来源必须保留。',2),
            ('偏好直接的说明','preference','','先给结论，必要时再展开原因。',2),
            ('历史方案暂时保留','reference','','这是一条可恢复的停用示例。',1),
        ]
        for title,kind,project,body,importance in samples[1:]+samples[:1]:
            note=store.dispatch('save',{'key':title,'title':title,'body':body,'scope':'project' if project else 'global','kind':kind,'importance':importance,'evidence':{'kind':'user','origin':'虚构示例 · 仅用于界面演示','text':'希望保留以后仍能复用的结论，并且能随时查看、更正和删除。'}},project)
            if title=='历史方案暂时保留':
                store.dispatch('mute',{'id':note['id'],'expected_revision':1})
        service=create_server(directory)
        thread=threading.Thread(target=service.serve_forever,daemon=True)
        thread.start()
        info={'base':f'http://127.0.0.1:{service.server_port}','token':service.token}
        print(json.dumps(info),flush=True)
        sys.stdin.readline()
        service.shutdown()
        service.server_close()
        thread.join()


if __name__=='__main__':
    main()
