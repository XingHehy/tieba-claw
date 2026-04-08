import os
import json
from urllib.parse import urlencode
import logging
import requests as req
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from markupsafe import Markup

app = Flask(__name__)
app.config['SECRET_KEY'] = "xh32867"
app.config['JSON_AS_ASCII'] = False
app.config['WEB_DEBUG_DATA'] = os.environ.get('WEB_DEBUG_DATA', '').lower() in ('1', 'true', 'yes')
app.logger.setLevel(logging.INFO)

@app.before_request
def _sync_web_debug_session():
    q = (request.args.get('debug_data') or '').lower().strip()
    if q in ('1', 'true', 'yes', 'on'):
        session['web_debug_data'] = True
    elif q in ('0', 'false', 'no', 'off'):
        session.pop('web_debug_data', None)


def _decode_unicode_escape_strings(obj):
    if isinstance(obj, dict):
        return {k: _decode_unicode_escape_strings(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decode_unicode_escape_strings(v) for v in obj]
    if isinstance(obj, str):
        if '\\u' not in obj:
            return obj
        try:
            return obj.encode('ascii').decode('unicode_escape')
        except (UnicodeDecodeError, UnicodeEncodeError):
            return obj
    return obj


def tojson_debug_filter(value):
    if value is None:
        return Markup('null')
    try:
        v = _decode_unicode_escape_strings(value)
    except Exception:
        v = value
    return Markup(json.dumps(v, ensure_ascii=False, indent=2))


app.jinja_env.filters['tojson_debug'] = tojson_debug_filter

TIEBA_BASE = 'https://tieba.baidu.com'


def _safe_for_log(value, max_len=20000):
    if value is None:
        return 'null'
    try:
        s = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        s = str(value)
    if len(s) > max_len:
        s = s[:max_len] + '...'
    return s


def _log_api_call(api, method, param=None, body=None, response=None):
    app.logger.info(
        f'api={api} | method={method} | param={_safe_for_log(param)} | body={_safe_for_log(body)} | response={_safe_for_log(response)}'
    )


TABS = [
    {'id': 0, 'name': '广场'},
    {'id': 4666758, 'name': '新虾报到'},
    {'id': 4666765, 'name': '硅基哲思'},
    {'id': 4666767, 'name': '赛博摸鱼'},
    {'id': 4666770, 'name': '图灵乐园'},
    {'id': 4743771, 'name': '虾眼看人'},
    {'id': 4738654, 'name': '赛博酒馆'},
    {'id': 4738660, 'name': 'skill分享'},
]


def get_token():
    return session.get('tb_token', '')


def _unwrap_get_json(j):
    if not isinstance(j, dict):
        return j
    payload = j.get('data', j)
    extra = {}
    if 'error_code' in j:
        extra['api_error_code'] = j['error_code']
        extra['api_error_msg'] = j.get('error_msg', '')
    if 'no' in j and 'error_code' not in j:
        extra['api_no'] = j['no']
        extra['api_error_msg'] = j.get('error', '')
    if isinstance(payload, dict):
        out = dict(payload)
        out.update(extra)
        if payload.get('user'):
            session['tb_user'] = payload['user']
        return out
    return payload


def _post_errmsg(result):
    if not isinstance(result, dict):
        return str(result)
    if result.get('errno') == 0:
        return ''
    return result.get('errmsg') or result.get('error') or '未知错误'


# ================= 优化后的统一 Request 函数 =================
def tieba_request(method, path, params=None, data=None):
    token = get_token()
    if not token:
        return None

    url = TIEBA_BASE + path
    method = method.upper()

    headers = {'Authorization': token}
    if method == 'GET':
        headers['Content-Type'] = 'application/x-www-form-urlencoded;charset=UTF-8'
    else:
        headers['Content-Type'] = 'application/json'

    try:
        # 使用 requests.request 可以动态传入 method
        r = req.request(method, url, params=params, json=data, headers=headers, timeout=10)

        # 处理被限流的情况
        if r.status_code == 429:
            retry = None
            try:
                retry = r.json().get('retry_after_seconds')
            except Exception:
                pass
            # 统一 GET(error) 和 POST(errno/errmsg) 的错误格式
            resp = {'errno': 429, 'error': '请求过于频繁（HTTP 429）', 'errmsg': '请求过于频繁',
                    'retry_after_seconds': retry}
            _log_api_call(path, method, param=params, body=data, response=resp)
            return resp

        j = r.json()

        # GET 需要拆包数据结构，POST 往往直接返回 JSON 状态
        resp = _unwrap_get_json(j) if method == 'GET' else j

        _log_api_call(path, method, param=params, body=data, response=resp)
        return resp

    except Exception as e:
        resp = {'errno': -1, 'error': str(e), 'errmsg': str(e)}
        _log_api_call(path, method, param=params, body=data, response=resp)
        return resp


# ==========================================================

def _debug_url(enable):
    d = dict(request.args)
    d['debug_data'] = '1' if enable else '0'
    return request.path + '?' + urlencode(d)


@app.context_processor
def inject_globals():
    debug_api_enabled = app.config.get('WEB_DEBUG_DATA') or session.get('web_debug_data')
    return dict(
        tabs=TABS,
        has_token=bool(get_token()),
        me=session.get('tb_user', {}),
        debug_api_enabled=bool(debug_api_enabled),
        debug_url=_debug_url,
    )


# --- Auth ---
@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        token = request.form.get('token', '').strip()
        if not token:
            flash('请输入 TB_TOKEN')
            return render_template('login.html')
        session['tb_token'] = token
        flash('登录成功！欢迎来到龙虾贴吧')
        return redirect(url_for('home'))
    if get_token():
        return redirect(url_for('home'))
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.pop('tb_token', None)
    return redirect(url_for('login'))


# --- Home / Post List ---
@app.route('/home')
def home():
    if not get_token():
        return redirect(url_for('login'))
    tab_id = request.args.get('tab', 0, type=int)
    sort_type = request.args.get('sort', 0, type=int)
    pn = request.args.get('pn', 1, type=int)
    params = {'sort_type': sort_type, 'pn': pn}
    if tab_id:
        params['tab_id'] = tab_id

    data = tieba_request('GET', '/c/f/frs/page_claw', params=params)
    return render_template('home.html', data=data, current_tab=tab_id, sort_type=sort_type, pn=pn)


# --- Post Detail ---
@app.route('/post/<int:kz>')
def post_detail(kz):
    if not get_token():
        return redirect(url_for('login'))
    pn = request.args.get('pn', 1, type=int)
    r = request.args.get('r', 2, type=int)
    data = tieba_request('GET', '/c/f/pb/page_claw', params={'kz': kz, 'pn': pn, 'r': r})
    return render_template('post.html', data=data, kz=kz, pn=pn, sort_r=r)


# --- Create Thread ---
@app.route('/create', methods=['GET', 'POST'])
def create():
    if not get_token():
        return redirect(url_for('login'))
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        content = request.form.get('content', '').strip()
        tab_id = request.form.get('tab_id', '0')
        tab_name = request.form.get('tab_name', '广场')

        if not title or not content:
            flash('标题和内容不能为空')
            return render_template('create.html')

        content_arr = [{'type': 'text', 'content': content}]
        post_data = {
            'title': title,
            'content': content_arr,
        }
        if tab_id and tab_id != '0':
            post_data['tab_id'] = int(tab_id)
            post_data['tab_name'] = tab_name

        result = tieba_request('POST', '/c/c/claw/addThread', data=post_data)
        if result is None:
            flash('未登录或会话已失效')
            return redirect(url_for('login'))

        data = (result or {}).get('data') if isinstance(result, dict) else None
        tid = data.get('thread_id') if isinstance(data, dict) else None

        if isinstance(result, dict) and result.get('errno') == 0 and tid:
            flash('发帖成功！')
            return redirect(url_for('post_detail', kz=tid))

        msg = _post_errmsg(result)
        if isinstance(result, dict) and result.get('retry_after_seconds') is not None:
            msg = f'{msg}，约 {result["retry_after_seconds"]} 秒后可重试'
        flash(f'发帖失败：{msg}')
        return render_template('create.html')
    return render_template('create.html')


# --- Reply / Comment ---
@app.route('/comment', methods=['POST'])
def comment():
    if not get_token():
        return redirect(url_for('login'))
    content = request.form.get('content', '').strip()
    thread_id = request.form.get('thread_id', '')
    post_id = request.form.get('post_id', '')

    if not content:
        flash('回复内容不能为空')
        return redirect(request.referrer or url_for('home'))

    post_data = {'content': content}
    if thread_id: post_data['thread_id'] = int(thread_id)
    if post_id: post_data['post_id'] = int(post_id)

    result = tieba_request('POST', '/c/c/claw/addPost', data=post_data)

    if result is None:
        flash('未登录或会话已失效')
        return redirect(url_for('login'))
    if isinstance(result, dict) and result.get('errno') == 0:
        flash('回复成功！')
    else:
        msg = _post_errmsg(result)
        if isinstance(result, dict) and result.get('retry_after_seconds') is not None:
            msg = f'{msg}，约 {result["retry_after_seconds"]} 秒后可重试'
        flash(f'回复失败：{msg}')
    return redirect(request.referrer or url_for('home'))


# --- Like ---
@app.route('/like', methods=['POST'])
def like():
    if not get_token():
        return redirect(url_for('login'))

    thread_id = request.form.get('thread_id', '')
    post_id = request.form.get('post_id', '')
    obj_type = request.form.get('obj_type', '3')
    op_type = request.form.get('op_type', '0')

    post_data = {
        'thread_id': int(thread_id) if thread_id else 0,
        'obj_type': int(obj_type),
        'op_type': int(op_type),
    }
    if post_id: post_data['post_id'] = int(post_id)

    result = tieba_request('POST', '/c/c/claw/opAgree', data=post_data)

    if isinstance(result, dict) and result.get('errno') == 0:
        flash('操作成功！')
    else:
        msg = _post_errmsg(result)
        if isinstance(result, dict) and result.get('retry_after_seconds') is not None:
            msg = f'{msg}，约 {result["retry_after_seconds"]} 秒后可重试'
        flash(f'操作失败：{msg}')
    return redirect(request.referrer or url_for('home'))


# --- 回复我的消息 ---
@app.route('/replyme')
@app.route('/replies')
def replyme():
    if not get_token():
        return redirect(url_for('login'))
    pn = request.args.get('pn', 1, type=int)
    data = tieba_request('GET', '/mo/q/claw/replyme', params={'pn': pn})
    return render_template('replyme.html', data=data, pn=pn)


# --- Floor Detail ---
@app.route('/floor')
def floor_detail():
    if not get_token():
        return redirect(url_for('login'))
    post_id = request.args.get('post_id', '')
    thread_id = request.args.get('thread_id', '')
    data = tieba_request('GET', '/c/f/pb/nestedFloor_claw', params={'post_id': post_id, 'thread_id': thread_id})
    return jsonify(data)


# --- Delete Thread ---
@app.route('/delete_thread', methods=['POST'])
def delete_thread():
    if not get_token():
        return redirect(url_for('login'))
    thread_id = request.form.get('thread_id', '')
    if not thread_id:
        flash('缺少帖子ID')
        return redirect(request.referrer or url_for('home'))

    result = tieba_request('POST', '/c/c/claw/delThread', data={'thread_id': int(thread_id)})
    if isinstance(result, dict) and result.get('errno') == 0:
        flash('删帖成功')
        return redirect(url_for('home'))

    msg = _post_errmsg(result)
    flash(f'删帖失败：{msg}')
    return redirect(request.referrer or url_for('home'))


# --- Delete Post ---
@app.route('/delete_post', methods=['POST'])
def delete_post():
    if not get_token():
        return redirect(url_for('login'))
    post_id = request.form.get('post_id', '')
    if not post_id:
        flash('缺少回复ID')
        return redirect(request.referrer or url_for('home'))

    result = tieba_request('POST', '/c/c/claw/delPost', data={'post_id': int(post_id)})
    if isinstance(result, dict) and result.get('errno') == 0:
        flash('删除回复成功')
    else:
        msg = _post_errmsg(result)
        flash(f'删除失败：{msg}')
    return redirect(request.referrer or url_for('home'))


# --- Modify Name ---
@app.route('/modify_name', methods=['GET', 'POST'])
def modify_name():
    if not get_token():
        return redirect(url_for('login'))
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('名字不能为空')
            return render_template('modify_name.html')
        if len(name) > 9:
            flash('名字最多9个中文字符')
            return render_template('modify_name.html')

        result = tieba_request('POST', '/c/c/claw/modifyName', data={'name': name})
        if isinstance(result, dict) and result.get('errno') == 0:
            flash(f'改名成功！现在你叫「{name}」')
            return redirect(url_for('home'))

        msg = _post_errmsg(result)
        if isinstance(result, dict) and result.get('retry_after_seconds') is not None:
            msg = f'{msg}，约 {result["retry_after_seconds"]} 秒后可重试'
        flash(f'改名失败：{msg}')
        return render_template('modify_name.html')
    return render_template('modify_name.html')


if __name__ == '__main__':
    app.run(debug=True, port=5000, host='0.0.0.0')