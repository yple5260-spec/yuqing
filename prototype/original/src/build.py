"""从 PagePrd 模板装配单文件演示；源码不构成运行依赖。"""
from pathlib import Path
from html import escape
import re
import urllib.request

BASE=Path(__file__).resolve().parent
OUT=BASE.parent
TEMPLATE=Path('C:/Users/iuya/.cc-switch/skills/PagePrd/assets/template.html')
specs=[
('overview','事件与态势','按同一查询范围查看态势与用户事件，定位重点待办。',[
('事件台账检索','list','输入关键词、平台、评估状态后查询；条件取交集，重置恢复全部。每页5条。',['事件 / 用户','传播风险','评估状态','同步状态','办理'],'按当前构造记录顺序；新增记录置于首行。'),
('态势统计与联动','visual','点击待办数量或等级条过滤事件明细；清除快捷筛选恢复范围。',['用户事件评估数 = 当前查询记录数；事件数按event_id去重；链接数为纳库内容数。','待补证/核实 = 绑定未确认或评估状态为证据不足的用户事件数；同步待办按任务计数。','等级分布仅当前有效已确认版，不纳入未评估或证据不足。','双快照互动图为同一批有效配对链接前/后次观测累计互动之和；下降或晚于评估时点排除，观测时点范围明示；各平台数为纳库链接条数。'],''),
('事件办理入口','detail','点击事件或办理入口，选中用户事件并进入关联、评估或同步；跨菜单保持上下文。',['事件编号','稳定用户标识','用户称呼','平台','结果版本','绑定状态'],''),
('录入事件与链接','form','点击录入事件打开居中表单，保存后进入链接与绑定。',['事件编号','用户称呼','事件摘要','平台','手机号','原始链接','工单或业务来源'],'')]),
('links','链接与绑定','登记来源并核实手机号归属；保留绑定更正和链接去重依据。',[
('绑定核实与更正','form-edit','业务复核人员打开核实/更正弹窗，填写手机号、来源、结论和依据后保存。',['核验手机号','绑定结论','来源凭据与核验方式','核验或更正说明'],''),
('链接列表与批量导入','list','查看已纳库链接；批量导入弹窗按7列制表符逐行校验并保留成功记录；重复链接提示现有归属。',['平台 / 内容','来源','快照','操作'],'当前用户事件的纳库顺序。'),
('解除绑定','action','业务复核人员填写解除依据后解除绑定，生成待重评版本。',['保留历史绑定与旧结果。','旧版未执行同步任务冻结，已同步任务进入待更正。','不根据昵称、头像或内容相似度推断手机号归属。'],''),
('录入事件与链接','form','录入链接使用与首页相同表单和去重逻辑；手机号可暂缺，先保存待核实记录。',['事件编号','用户称呼','事件摘要','平台','手机号','原始链接','工单或业务来源'],'')]),
('evidence','证据与指标','查看原始内容摘录、观测快照及缺失原因，补充可核验指标。',[
('证据详情与快照补充','form','选择当前链接；录入采集人员打开快照弹窗补充摘要、发布时间、观测时间和三项累计互动，追加保存。',['内容链接','内容摘录 / 可核验摘要','发布时间','观测时间','累计点赞','累计评论','累计转发 / 分享','原创属性','快照来源与依据'],''),
('账号与互动核验','form-edit','录入采集人员打开核验弹窗，补充粉丝数、大V互动及D6依据，形成新评估版本。',['最高可见粉丝量','大 V 互动','D6 描述','核验依据'],''),
('采样与缺失处理','action','查看单/双快照差异与缺失说明，再进入评估。',['只有单快照不计算互动频率，标记NOT_COLLECTED。','同一组链接两次观测间隔24小时±30分钟，按实际小时数折算；下降或异常窗口标记未知。','观测时间不晚于固定评估时点，指标为非负整数。','全部为构造内容和人工来源，不代表真实平台已接入。'],'')]),
('assessment','评估与复核','展示评分上下界、证据限制和人工复核过程，保留历史结果版本。',[
('六维评分与分数区间','visual','选择用户事件查看实时证据计算；每项可追溯到当前证据。',['S = D1+D2+D3+D4+D5；D6不计分。','完全未知项按0至该维度最高分形成区间；L为各项下界之和，U为上界之和。','L与U同档才提供建议色；跨档标证据不足；不重新归一化已知项。','正式确认要求已确认绑定、可核验内容与证据时间，至少一条相关内容。'],''),
('计算提交与人工复核','form-edit','录入采集人员计算并提交；另一位业务复核人员通过、退回或有据调整。调整需填写审批依据。',['复核动作','调整后等级','复核意见 / 调整依据与审批记录'],''),
('结果版本与重评','detail','查看提交人与复核人、原分数与最终等级。发起重评、新证据、绑定变化生成新版本，历史只读。',['提交人','复核人','当前状态','复核意见','历史版本 / 时间 / 原因'], '')]),
('sync','同步与更正','提供已确认摘要，记录人工录入回执、核对与目标更正全过程。',[
('确认结果摘要与任务生成','action','仅当前有效已确认结果可复制/导出摘要或生成同步任务。',['复制与导出不改变同步状态。','同一结果版本重复生成返回原任务。','摘要含用户事件、脱敏手机号、分数等级、证据限制、结果及规则版本。','本次无目标系统自动写入。'],''),
('同步回执与核对','form','同步人员登记目标记录号、录入时间和说明；业务复核人员勾选核对后确认。',['目标记录号 / 更正凭据号','目标录入时间','办理说明','核对确认'],''),
('失败与旧版更正','action','登记失败后保留原任务重试；旧版已同步后发生错绑或重评，办理目标更正并核对。',['普通同步：待同步→待核对→已同步。','更正：待更正→更正待核对→更正完成；登记更正回执不能直接关闭。','结果版本变化阻断旧任务办理；未执行任务冻结。','操作人与核对人以稳定人员ID分离。'],'')]),
('rules','规则与审计','核对演示规则与平台边界，查看本地关键操作记录。',[
('演示规则查看','detail','查看需求书V0.1分档与等级；与截图差异显著提示，未提供未经审批的生效操作。',['D1至D5分档','D6不计分','等级阈值','规则版本 / 待审批标识'],''),
('平台接入边界','detail','查看微博、小红书、抖音、快手、B站、贴吧的人工录入状态。',['平台','人工录入状态','自动接入待确认'],''),
('操作审计与重置','list','查看最近30条本地构造操作日志。规则管理人员确认后重置构造业务数据，需求说明不清除。',['时间 / 人员','操作','对象'],'日志按记录顺序倒序展示。')])]

def tb(headers,rows):
 return '<table class="spec-tb"><thead><tr>'+''.join('<th>'+escape(h)+'</th>' for h in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td'+(' class="lock"' if lock else ' data-edit')+'>'+escape(str(v))+'</td>' for v,lock in row)+'</tr>' for row in rows)+'</tbody></table>'
def sub(k,v,editable=True):return '<div class="sub"><div class="sub-k">'+k+'</div><div class="sub-v"'+(' data-edit' if editable else '')+'>'+v+'</div></div>'
def panes():
 result=[]
 for menu,label,purpose,items in specs:
  permission=tb(['功能点 🔒','操作权限','数据权限','备注'],[[(i[0],True),('',False),('',False),('',False)] for i in items])
  overview='<section><h3>一、页面概述</h3>'+sub('菜单位置',label)+sub('解决问题',purpose)+sub('权限说明',permission,False)+'</section>'
  cards=[]
  for j,(name,kind,interaction,fields,sorting) in enumerate(items):
   k=chr(0x2460+j);extra=''
   if kind=='list':
    extra+=sub('状态流转','')+sub('列表字段',tb(['元素 🔒','展示逻辑','备注'],[[(f,True),('以左侧对应字段展示；状态为文字与颜色并列。',False),('',False)] for f in fields]),False)+sub('数据排序',sorting)
   elif kind.startswith('form'):
    edit=kind=='form-edit';rows=[]
    for f in fields:
     optional=f in ['手机号','最高可见粉丝量','调整后等级']
     validation=''+('可暂缺，按未知或待核实处理。' if optional else '按当前动作校验，不得为空。')
     if '手机号' in f:validation='11位或138****1234构造脱敏格式；保存只保留脱敏值。'
     if '累计' in f or '粉丝' in f:validation='非负安全整数；粉丝可留空标记未知。'
     rows.append([(f,True),('输入 / 选择',True),('否' if optional else '是',not edit),(validation,not edit),('回显当前值' if edit else '',not edit),('',False)])
    extra+=sub('字段说明',tb(['元素 🔒','类型 🔒','必填','校验规则','默认值','备注'],rows),False)
    rules=['保存时按演示人员ID与角色再次检查动作条件；不构成正式身份认证。','数据变化保留旧版本，按绑定、证据、复核与同步门槛办理。']
    extra+=sub('业务规则','<div class="rules">'+''.join('<div>'+r+'</div>' for r in rules)+'</div>')
   elif kind=='detail':extra+=sub('展示元素',tb(['字段 🔒','展示逻辑','备注'],[[(f,True),('保留当前记录与版本上下文。',False),('',False)] for f in fields]),False)
   else:extra+=sub('数据逻辑' if kind=='visual' else '业务规则','<div class="rules">'+''.join('<div>'+escape(f)+'</div>' for f in fields)+'</div>')
   cards.append('<div class="fn item" data-k="'+k+'"><div class="fn-t"><span class="num">'+k+'</span><span class="fn-name">'+name+'</span></div>'+sub('交互',escape(interaction))+extra+'</div>')
  result.append('<div class="menu-pane'+(' hidden' if menu!='overview' else '')+'" data-menu="'+menu+'" data-label="'+label+'">'+overview+'<section><h3>二、功能说明</h3>'+''.join(cards)+'</section></div>')
 return ''.join(result)

def build():
 runtime=BASE/'vue.global.prod.js'
 if not runtime.exists():
  with urllib.request.urlopen('https://unpkg.com/vue@3/dist/vue.global.prod.js',timeout=45) as r: runtime.write_bytes(r.read())
 vue=runtime.read_text(encoding='utf-8')
 template=TEMPLATE.read_text(encoding='utf-8')
 html='''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>外部舆情评估系统 · 研判调度台</title><style>'''+(BASE/'style.css').read_text(encoding='utf-8')+'''</style></head>
<body data-module="外部舆情评估系统" data-active-menu="overview" data-spec="shown"><div class="layout"><div id="protoWrap"><div id="app" v-cloak></div><button id="specToggle" type="button" onclick="toggleSpec()">隐藏需求说明</button></div><div id="specDivider"><i></i><span>分割线</span><i></i></div><aside id="specAside"><h2 class="spec-heading">需求说明</h2><div class="spec-hint"><div>说明跟随左侧菜单切换，一菜单一份完整说明。</div><div>点击底部“编辑需求说明”，可编辑内容自动保存。</div><div>左侧编号定位说明；右侧编号高亮对应功能。</div><div>带锁定标记的字段与原型绑定，不可编辑。</div><div>修改存于浏览器；请导出HTML交给Agent合并回磁盘。</div></div><div id="specBody">'''+panes()+'''</div><div class="spec-tools"><button id="btnEdit" onclick="setEdit(!editing)">编辑需求说明</button><button onclick="addSection()">+ 新增章节</button><button onclick="exportHtml()">导出 HTML</button><button onclick="resetSpec()">恢复默认</button><div id="saveTip" aria-live="polite"></div></div></aside></div>
<script type="text/x-template" id="protoTpl">'''+(BASE/'prototype.html').read_text(encoding='utf-8')+'''</script>
<script id="runtimeKey">var PROTO_KEY = "proto-yuqing-20261029-v3";</script><script id="embeddedState">window.EMBEDDED_STATE = null;</script>
<script>'''+vue+'''</script><script>'''+(BASE/'core.js').read_text(encoding='utf-8')+'''</script><script>'''+(BASE/'spec.js').read_text(encoding='utf-8')+'''</script><script>'''+(BASE/'app.js').read_text(encoding='utf-8')+'''</script></body></html>'''
 # 保留 PagePrd 模板所规定的挂载、说明区和导出结构，内容替换为本系统。
 assert 'id="protoTpl"' in template and 'id="specBody"' in template
 OUT.mkdir(exist_ok=True)
 (OUT/'index.html').write_text(html,encoding='utf-8')
 print('BUILT',OUT/'index.html',len(html),'chars; Vue:',vue[:120].replace('\n',' '))

if __name__=='__main__':build()
