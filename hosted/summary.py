"""Reader's-companion summary: Claude API (structured JSON output), with
Ollama Cloud as a backup if the Claude call fails (no credits, outage).

Ollama models are tried in order: Ollama retires cloud models without much
notice (see morning-dash briefing.ts), so keep a fallback list in MODELS.
"""
import html, json, os, re, urllib.request

import anthropic

CLAUDE_MODEL = os.environ.get('CLAUDE_MODEL', 'claude-opus-5-5')
claude = anthropic.Anthropic() if os.environ.get('ANTHROPIC_API_KEY') else None

HOST = os.environ.get('OLLAMA_HOST', 'https://ollama.com')
KEY = os.environ.get('OLLAMA_API_KEY', '')
MODELS = [m.strip() for m in os.environ.get('MODELS', 'gpt-oss:120b,gpt-oss:20b').split(',') if m.strip()]

SCHEMA = {
    'type': 'object',
    'properties': {
        'tldr': {'type': 'string'},
        'ideas': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'title': {'type': 'string'}, 'detail': {'type': 'string'}}, 'required': ['title', 'detail']}},
        'actions': {'type': 'array', 'items': {'type': 'string'}},
    },
    'required': ['tldr', 'ideas', 'actions'],
}
# Claude's structured outputs need closed objects
CLAUDE_SCHEMA = json.loads(json.dumps(SCHEMA))
CLAUDE_SCHEMA['additionalProperties'] = False
CLAUDE_SCHEMA['properties']['ideas']['items']['additionalProperties'] = False

PROMPT = """You are writing a one-page "reader's companion" printed after an article.
Read the WHOLE article below, then return JSON:
- tldr: 3-4 sentences (max ~70 words) naming the author and the core argument.
- ideas: exactly 7 key ideas from the article. title = 2-6 word bold lead-in ending without a period; detail = one sentence (max ~30 words).
- actions: exactly 7 concrete, imperative action points a reader could do this week (max ~20 words each).
Stay faithful to the article; do not invent claims. Plain text only, no markdown.

Title: {title}
Author: {author}

ARTICLE:
{text}"""


def _claude(content):
    r = claude.beta.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=16000,
        output_config={'effort': 'medium', 'format': {'type': 'json_schema', 'schema': CLAUDE_SCHEMA}},
        betas=['server-side-fallback-2026-07-01'],
        fallbacks='default',
        messages=[{'role': 'user', 'content': content}],
    )
    if r.stop_reason == 'refusal':
        raise RuntimeError('Claude declined the request')
    if r.stop_reason == 'max_tokens':
        raise RuntimeError('Claude hit max_tokens')
    return json.loads(next(b.text for b in r.content if b.type == 'text')), r.model


def _chat(model, content):
    req = urllib.request.Request(
        f'{HOST}/api/chat',
        data=json.dumps({'model': model, 'stream': False, 'format': SCHEMA,
                         'options': {'temperature': 0.3},
                         'messages': [{'role': 'user', 'content': content}]}).encode(),
        headers={'Content-Type': 'application/json', **({'Authorization': f'Bearer {KEY}'} if KEY else {})})
    with urllib.request.urlopen(req, timeout=180) as r:
        msg = json.load(r)['message']['content']
    msg = re.sub(r'^```(?:json)?|```$', '', msg.strip()).strip()
    return json.loads(msg)


def summarise(title, author, text, log=print):
    """-> ({'tldr': html, 'ideas': [html], 'actions': [html]}, model_name)"""
    errors = []
    attempts = []
    if claude:
        attempts.append(('claude', lambda: _claude(PROMPT.format(title=title, author=author or 'unknown', text=text))))
    for m in MODELS:   # Ollama context is smaller: trim long articles
        attempts.append((m, lambda m=m: (_chat(m, PROMPT.format(title=title, author=author or 'unknown', text=text[:60000])), m)))
    for name, call in attempts:
        try:
            d, model = call()
            ideas, actions = d['ideas'][:7], d['actions'][:7]
            if not d['tldr'] or len(ideas) < 3 or len(actions) < 3:
                raise ValueError('summary too thin')
            e = lambda x: html.escape(re.sub(r'\*\*|`', '', x))
            return {
                'tldr': e(d['tldr'].strip()),
                'ideas': [f'<b>{e(i["title"].strip().rstrip("."))}.</b> {e(i["detail"].strip())}' for i in ideas],
                'actions': [e(a.strip()) for a in actions],
            }, model
        except Exception as ex:
            log(f'[summary] {name} failed: {ex}')
            errors.append(f'{name}: {ex}')
    raise RuntimeError('; '.join(errors) or 'no models configured')
