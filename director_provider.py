"""Opt-in text-only planners. Fixed HTTPS destinations, no files or tool execution."""
import json
import os
import urllib.error
import urllib.request
from director import compact

PROVIDERS={'openai':('https://api.openai.com/v1/responses','OpenAI'),
           'anthropic':('https://api.anthropic.com/v1/messages','Anthropic')}


def settings(provider):
    if provider not in PROVIDERS:raise ValueError('请选择支持的 AI 服务')
    prefix='MEMORY_DIRECTOR_'+provider.upper()
    return os.environ.get(prefix+'_KEY',''),os.environ.get(prefix+'_MODEL','')


def status():
    return [{'id':p,'label':label,'configured':bool(all(settings(p))),'destination':url.split('/')[2]} for p,(url,label) in PROVIDERS.items()]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError('AI 接口重定向已拒绝')


def plan(provider,payload):
    key,model=settings(provider)
    if not key or not model:raise ValueError('主机尚未配置该 AI 服务的专用 API 密钥与模型名；可继续用本地规则')
    if len(compact(payload).encode('utf-8'))>100000:raise ValueError('候选摘要过大，请减少素材或备注')
    content=compact({k:v for k,v in payload.items() if k not in ('system','output_schema')})
    schema=payload['output_schema'];headers={'Content-Type':'application/json'}
    if provider=='openai':
        body={'model':model,'store':False,'instructions':payload['system'],'input':content,'max_output_tokens':6500,
              'text':{'format':{'type':'json_schema','name':'memory_edit','strict':True,'schema':schema}}}
        headers['Authorization']='Bearer '+key
    else:
        body={'model':model,'max_tokens':6500,'system':payload['system'],'messages':[{'role':'user','content':content}],
              'output_config':{'format':{'type':'json_schema','schema':schema}}}
        headers.update({'x-api-key':key,'anthropic-version':'2023-06-01'})
    request=urllib.request.Request(PROVIDERS[provider][0],data=compact(body).encode('utf-8'),headers=headers,method='POST')
    try:
        with urllib.request.build_opener(NoRedirect()).open(request,timeout=90) as response:
            raw=response.read(1024*1024+1)
        if len(raw)>1024*1024:raise ValueError('AI 响应过大，已拒绝')
        result=json.loads(raw)
        if provider=='openai':
            if result.get('status')!='completed':raise ValueError('AI 响应未完整完成；没有修改原方案')
            texts=[b['text'] for item in result.get('output',[]) if item.get('type')=='message' for b in item.get('content',[]) if b.get('type')=='output_text']
        else:
            if result.get('stop_reason')!='end_turn':raise ValueError('AI 响应未完整完成；没有修改原方案')
            texts=[b['text'] for b in result.get('content',[]) if b.get('type')=='text']
        return json.loads(''.join(texts))
    except urllib.error.HTTPError as error:
        # Provider bodies may echo private input. Never include them or credentials in logs/errors.
        raise ValueError(f'AI 接口返回 HTTP {error.code}；请在主机核对模型是否支持结构化输出、密钥与额度。没有自动重试或额外调用。') from None
    except (OSError,TimeoutError,UnicodeError,json.JSONDecodeError,KeyError,TypeError,AttributeError):
        raise ValueError('AI 请求超时、网络失败或响应无效；原方案保留，请自行决定是否重试。') from None
