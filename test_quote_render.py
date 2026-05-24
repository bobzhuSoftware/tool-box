import sys; sys.path.insert(0, '.')
from wechat_worker import _extract_content

# Simulate a quote message
msg = {
    'type': 49,
    'content': '<?xml version="1.0"?><msg><appmsg appid="" sdkver="0"><title>不会暴雷 只会烂尾</title><type>57</type><refermsg><type>1</type><displayname>有正事我不干</displayname><content>共有产权房暴雷吗</content></refermsg></appmsg></msg>',
    'compress': None
}
result = _extract_content(msg)
print("=== TXT output ===")
print(result)

# Quote where referenced message is also type 49 (nested XML)
msg2 = {
    'type': 49,
    'content': '<?xml version="1.0"?><msg><appmsg appid="" sdkver="0"><title>有</title><type>57</type><refermsg><type>49</type><displayname>Doreen Zhu</displayname><content>&lt;?xml version="1.0"?&gt;&lt;msg&gt;&lt;appmsg&gt;&lt;title&gt;还有吗&lt;/title&gt;&lt;type&gt;57&lt;/type&gt;&lt;/appmsg&gt;&lt;/msg&gt;</content></refermsg></appmsg></msg>',
    'compress': None
}
result2 = _extract_content(msg2)
print("\n=== TXT output (nested quote) ===")
print(result2)
