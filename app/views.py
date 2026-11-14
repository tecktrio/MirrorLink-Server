import base64
import os
import time
import json
import queue
import asyncio
import hashlib
import secrets
import re
import html as html_lib
import tempfile
import uuid
import ipaddress
import socket
import urllib.parse
import requests
from datetime import datetime, timedelta, timezone
from bson import ObjectId
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.contrib.auth.hashers import make_password, check_password
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import administrators_collection, users_collection, plans_collection, mirror_collection, sites_collection, content_collection, menu_templates_collection, menus_collection
from .billing import PLAN, administrator_ids, count_administrator_mirrors, count_administrator_sites
from s3_config import upload_file_to_s3, delete_file_from_s3

MENU_ARTWORK_VERSION = 2


def get_administrator(login_key=None):
    if login_key:
        return administrators_collection.find_one({'login_key': login_key})
    return None


def plan_is_active(user):
    subscription = (user or {}).get('subscription', {})
    return subscription.get('status') == 'active' and bool(subscription.get('plan_id'))


def send_email_otp(email, purpose):
    code = f"{secrets.randbelow(1000000):06d}"
    otp_hash = hashlib.sha256(code.encode()).hexdigest()
    record = {'email_id': email.lower().strip(), 'purpose': purpose, 'otp_hash': otp_hash,
              'expires_at': datetime.now(timezone.utc) + timedelta(minutes=10), 'created_at': datetime.now(timezone.utc)}
    # Keep one current challenge per email and purpose.
    users_collection.update_one({'email_id': record['email_id']}, {'$set': {f'otp.{purpose}': record}}, upsert=True)
    try:
        subject = 'Your MirrorLink verification code'
        text_body = f'Your MirrorLink verification code is {code}. It expires in 10 minutes.'
        html_body = f'''<!doctype html><html><body style="margin:0;background:#f5f5f5;color:#080808;font-family:Arial,Helvetica,sans-serif">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f5f5f5;padding:40px 16px"><tr><td align="center">
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:560px;background:#fff;border:1px solid #e8e8e8">
              <tr><td style="padding:24px 30px;border-bottom:1px solid #eee"><span style="display:inline-block;width:30px;height:30px;line-height:30px;text-align:center;background:#080808;color:#fff;font-size:18px;font-weight:bold;border-radius:3px">M</span><span style="margin-left:10px;vertical-align:7px;font-size:18px;font-weight:bold;letter-spacing:-.5px">MirrorLink</span></td></tr>
              <tr><td style="padding:36px 30px 12px"><div style="font-size:10px;font-weight:bold;letter-spacing:1.6px;color:#777">ACCOUNT VERIFICATION</div><h1 style="font-size:28px;line-height:1.15;letter-spacing:-1px;margin:14px 0 10px">Your code is ready.</h1><p style="font-size:14px;line-height:1.7;color:#666;margin:0">Enter this verification code to continue with your MirrorLink account.</p></td></tr>
              <tr><td style="padding:14px 30px 26px"><div style="background:#f5f5f5;padding:20px;text-align:center;font-size:32px;font-weight:bold;letter-spacing:9px">{code}</div><p style="font-size:12px;line-height:1.6;color:#777;margin:16px 0 0">This code expires in 10 minutes. If you did not request it, you can ignore this email.</p></td></tr>
              <tr><td style="padding:18px 30px;border-top:1px solid #eee;font-size:10px;color:#888">MirrorLink · Wireless display management</td></tr>
            </table>
          </td></tr></table></body></html>'''
        message = EmailMultiAlternatives(subject, text_body, getattr(settings, 'DEFAULT_FROM_EMAIL', None), [record['email_id']])
        message.attach_alternative(html_body, 'text/html')
        message.send(fail_silently=False)
    except Exception:
        users_collection.update_one({'email_id': record['email_id']}, {'$unset': {f'otp.{purpose}': ''}})
        raise


def consume_email_otp(email, code, purpose):
    email = (email or '').lower().strip()
    challenge = users_collection.find_one({'email_id': email}, {f'otp.{purpose}': 1}) or {}
    challenge = challenge.get('otp', {}).get(purpose, {})
    if not challenge or challenge.get('attempts', 0) >= 5:
        return False
    expires_at = challenge.get('expires_at')
    if not expires_at:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        return False
    if not secrets.compare_digest(challenge.get('otp_hash', ''), hashlib.sha256(str(code or '').encode()).hexdigest()):
        users_collection.update_one({'email_id': email}, {'$inc': {f'otp.{purpose}.attempts': 1}})
        return False
    users_collection.update_one({'email_id': email}, {'$unset': {f'otp.{purpose}': ''}})
    return True


def serialize_doc(obj):
    if isinstance(obj, ObjectId):
        return str(obj)
    if isinstance(obj, dict):
        return {k: serialize_doc(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [serialize_doc(v) for v in obj]
    return obj


MENU_TEMPLATE_SEEDS = [
    {
        "_id": "classic-menu",
        "name": "Classic Menu Board",
        "description": "A warm, readable menu with clear sections and prices.",
        "layout": "classic",
        "background": "#f5f0e6",
        "accent": "#9a4c2f",
        "html_template": "<div class='menu classic'><header><p>{{eyebrow}}</p><h1>{{title}}</h1><h2>{{subtitle}}</h2></header><main>{{items}}</main><footer>{{footer}}</footer></div>",
        "components": [
            {"key": "eyebrow", "label": "Small heading", "type": "text", "default": "FRESH & LOCAL"},
            {"key": "title", "label": "Menu title", "type": "text", "default": "Our Menu", "required": True},
            {"key": "subtitle", "label": "Subtitle", "type": "text", "default": "Made with care"},
            {"key": "accent", "label": "Accent color", "type": "color", "default": "#9a4c2f"},
            {"key": "background", "label": "Background color", "type": "color", "default": "#f5f0e6"},
            {"key": "items", "label": "Menu items", "type": "items", "required": True},
            {"key": "footer", "label": "Footer note", "type": "text", "default": "Thank you for visiting"},
        ],
    },
    {
        "_id": "modern-menu",
        "name": "Modern Grid",
        "description": "A bold two-column display for cafes and quick service.",
        "layout": "modern",
        "background": "#111827",
        "accent": "#f4c95d",
        "html_template": "<div class='menu modern'><header><p>{{eyebrow}}</p><h1>{{title}}</h1><h2>{{subtitle}}</h2></header><main>{{items}}</main><footer>{{footer}}</footer></div>",
        "components": [
            {"key": "eyebrow", "label": "Small heading", "type": "text", "default": "TODAY'S SPECIALS"},
            {"key": "title", "label": "Menu title", "type": "text", "default": "Cafe Menu", "required": True},
            {"key": "subtitle", "label": "Subtitle", "type": "text", "default": "Coffee · Brunch · Treats"},
            {"key": "accent", "label": "Accent color", "type": "color", "default": "#f4c95d"},
            {"key": "background", "label": "Background color", "type": "color", "default": "#111827"},
            {"key": "items", "label": "Menu items", "type": "items", "required": True},
            {"key": "footer", "label": "Footer note", "type": "text", "default": "Ask us about today's offers"},
        ],
    },
    {
        "_id": "photo-menu",
        "name": "Photo Feature Board",
        "description": "Image-forward menu cards with room for item photos.",
        "layout": "photo",
        "background": "#fffaf2",
        "accent": "#20735a",
        "html_template": "<div class='menu photo'><header><p>{{eyebrow}}</p><h1>{{title}}</h1><h2>{{subtitle}}</h2></header><main>{{items}}</main><footer>{{footer}}</footer></div>",
        "components": [
            {"key": "eyebrow", "label": "Small heading", "type": "text", "default": "FROM OUR KITCHEN"},
            {"key": "title", "label": "Menu title", "type": "text", "default": "Seasonal Picks", "required": True},
            {"key": "subtitle", "label": "Subtitle", "type": "text", "default": "A few favorites"},
            {"key": "accent", "label": "Accent color", "type": "color", "default": "#20735a"},
            {"key": "background", "label": "Background color", "type": "color", "default": "#fffaf2"},
            {"key": "items", "label": "Menu items", "type": "items", "required": True},
            {"key": "footer", "label": "Footer note", "type": "text", "default": "Made fresh every day"},
        ],
    },
]


def ensure_menu_templates():
    if menu_templates_collection.count_documents({}) == 0:
        menu_templates_collection.insert_many(MENU_TEMPLATE_SEEDS)


def render_menu_svg(menu, template, width=1920, height=1080):
    """Render saved menu HTML into a self-contained SVG image for cropping/import."""
    if template.get('custom_layout'):
        return render_custom_menu_svg(menu, template, width, height)
    data = menu.get('data', {})
    components = template.get('components', [])
    def field_value(key, default=''):
        component = next((item for item in components if item.get('key') == key), {})
        return html_lib.escape(str(data.get(key, component.get('default', default)) or ''), quote=True)

    def safe_color(key, default):
        value = str(data.get(key, default))
        return value if re.fullmatch(r'#[0-9a-fA-F]{6}', value) else default

    items = data.get('items') if isinstance(data.get('items'), list) else []
    accent = safe_color('accent', template.get('accent', '#9a4c2f'))
    background = safe_color('background', template.get('background', '#f5f0e6'))
    item_markup = []
    for item in items[:40]:
        if not isinstance(item, dict):
            continue
        name = html_lib.escape(str(item.get('name', '')), quote=True)
        price = html_lib.escape(str(item.get('price', '')), quote=True)
        description = html_lib.escape(str(item.get('description', '')), quote=True)
        image_url = str(item.get('image_url', '')).strip()
        image_markup = ''
        if image_url.startswith(('https://', 'http://', 'data:image/')):
            image_markup = f"<img src='{html_lib.escape(image_url, quote=True)}' style='width:90px;height:72px;object-fit:cover;border-radius:8px;margin-right:16px'/>"
        item_markup.append(
            f"<article>{image_markup}<div class='item-copy'><strong>{name}</strong>"
            f"<span class='price'>{price}</span><p>{description}</p></div></article>"
        )
    if template.get('custom_layout'):
        rendered = render_menu_html(menu, template)
    else:
        template_html = template.get('html_template', MENU_TEMPLATE_SEEDS[0]['html_template'])
        rendered = template_html.replace('{{eyebrow}}', field_value('eyebrow'))
        rendered = rendered.replace('{{title}}', field_value('title', menu.get('title', 'Menu')))
        rendered = rendered.replace('{{subtitle}}', field_value('subtitle'))
        rendered = rendered.replace('{{footer}}', field_value('footer'))
        rendered = rendered.replace('{{items}}', ''.join(item_markup))
    css = f"""
      * {{ box-sizing:border-box; }} html,body {{ margin:0;width:{width}px;height:{height}px;overflow:hidden; }}
      body {{ font-family:Arial,sans-serif;background:{background};color:#161616; }}
      .menu {{ width:{width}px;height:{height}px;padding:5.5% 7%;background:{background};overflow:hidden;display:flex;flex-direction:column; }}
      .modern {{ background:{background};color:#fff; }} .photo {{ background:{background}; }}
      header {{ text-align:center;border-bottom:2px solid {accent};padding-bottom:20px;margin-bottom:24px; }}
      header p {{ color:{accent};font-size:20px;letter-spacing:5px;font-weight:700;margin:0 0 12px; }}
      h1 {{ color:{accent};font-size:68px;line-height:1.05;margin:0 0 10px; }} h2 {{ font-size:24px;font-weight:400;margin:0;opacity:.75; }}
      main {{ display:grid;grid-template-columns:1fr 1fr;gap:12px 34px;flex:1;align-content:start;overflow:hidden; }}
      article {{ display:flex;align-items:center;min-height:104px;border-bottom:1px solid {accent}55;padding:8px 0; }}
      .item-copy {{ flex:1;min-width:0; }} article strong {{ font-size:26px; }} article .price {{ float:right;color:{accent};font-weight:700;font-size:24px; }}
      article p {{ margin:8px 0 0;opacity:.7;font-size:16px; }} footer {{ color:{accent};font-size:16px;text-align:center;padding-top:14px; }}
    """
    html_doc = f"<html xmlns='http://www.w3.org/1999/xhtml'><head><style>{css}</style></head><body>{rendered}</body></html>"
    svg = (f"<svg xmlns='http://www.w3.org/2000/svg' xmlns:xhtml='http://www.w3.org/1999/xhtml' "
           f"width='{width}' height='{height}' viewBox='0 0 {width} {height}'><foreignObject width='100%' height='100%'>{html_doc}</foreignObject></svg>")
    encoded = base64.b64encode(svg.encode('utf-8')).decode('ascii')
    return f'data:image/svg+xml;base64,{encoded}'


def render_custom_menu_svg(menu, template, width, height):
    """Render custom menu holders as native SVG so thumbnails and mobile browsers agree."""
    data = menu.get('data', {}) if isinstance(menu.get('data'), dict) else {}
    blocks = template.get('blocks', []) if isinstance(template.get('blocks'), list) else []
    saved_blocks = data.get('blocks', []) if isinstance(data.get('blocks'), list) else []
    saved_by_key = {str(block.get('holder_key')): block for block in saved_blocks if isinstance(block, dict) and block.get('holder_key')}
    esc = lambda value: html_lib.escape(str(value or ''), quote=True)
    def pct(value, default=0, minimum=0, maximum=100):
        try: return max(minimum, min(maximum, float(value)))
        except (TypeError, ValueError): return default
    embedded_images = {}
    def inline_image(src):
        if src.startswith('data:image/'):
            return src
        if src in embedded_images:
            return embedded_images[src]
        try:
            parsed = urllib.parse.urlparse(src)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
                return ''
            addresses = {entry[4][0] for entry in socket.getaddrinfo(parsed.hostname, None)}
            if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
                return ''
            response = requests.get(src, timeout=(3, 8), allow_redirects=False, stream=True)
            if response.status_code != 200 or not (response.headers.get('Content-Type', '').split(';')[0].lower().startswith('image/')):
                return ''
            mime = response.headers.get('Content-Type', '').split(';')[0].lower()
            if mime == 'image/svg+xml':
                return ''
            chunks, total = [], 0
            for chunk in response.iter_content(64 * 1024):
                total += len(chunk)
                if total > 2 * 1024 * 1024: return ''
                chunks.append(chunk)
            embedded_images[src] = f"data:{mime};base64,{base64.b64encode(b''.join(chunks)).decode('ascii')}"
            return embedded_images[src]
        except Exception:
            return ''
    bg = str(data.get('background') or template.get('background') or '#ffffff')
    if not re.fullmatch(r'#[0-9a-fA-F]{6}', bg): bg = '#ffffff'
    bg_image = str(data.get('background_image') or template.get('background_image') or '').strip()
    parts = [f"<rect width='{width}' height='{height}' fill='{bg}'/>"]
    if bg_image.startswith(('https://', 'http://', 'data:image/')):
        embedded_bg = inline_image(bg_image)
        if embedded_bg:
            parts.append(f"<image href='{esc(embedded_bg)}' x='0' y='0' width='{width}' height='{height}' preserveAspectRatio='xMidYMid slice'/>")
    def draw(block, value, index):
        if not isinstance(block, dict): return
        x, y = pct(block.get('x')), pct(block.get('y'))
        w, h = pct(block.get('width', 20), 20, 1), pct(block.get('height', 15), 15, 1)
        x, y, w, h = width*x/100, height*y/100, width*w/100, height*h/100
        clip = f'holder-{index}'
        parts.append(f"<defs><clipPath id='{clip}'><rect x='{x}' y='{y}' width='{w}' height='{h}'/></clipPath></defs>")
        parts.append(f"<g clip-path='url(#{clip})'>")
        if isinstance(value, str):
            value = {'image_url': value} if block.get('type') == 'image' else {'text': value}
        value = value if isinstance(value, dict) else {}
        if block.get('type') == 'image':
            src = str(value.get('image_url') or '').strip()
            if src.startswith(('https://', 'http://', 'data:image/')):
                src = inline_image(src)
            if src:
                fit = 'xMidYMid meet' if block.get('crop') == 'contain' else 'xMidYMid slice'
                parts.append(f"<image href='{esc(src)}' x='{x}' y='{y}' width='{w}' height='{h}' preserveAspectRatio='{fit}'/>")
        else:
            text = str(value.get('text') or block.get('text') or block.get('holder_label') or '')
            try: size = max(8, min(120, int(block.get('font_size', 24))))
            except (ValueError, TypeError): size = 24
            font = block.get('font_family') if block.get('font_family') in ('Arial', 'Georgia', 'Verdana', 'Tahoma') else 'Arial'
            color = str(block.get('color') or '#171717')
            if not re.fullmatch(r'#[0-9a-fA-F]{6}', color): color = '#171717'
            lines = text.splitlines() or ['']
            parts.append(f"<text x='{x}' y='{y + size}' fill='{color}' font-family='{font}' font-size='{size}' font-weight='{'700' if block.get('bold') else '400'}'>")
            for line_i, line in enumerate(lines[:30]):
                parts.append(f"<tspan x='{x}' dy='{'0' if line_i == 0 else size * 1.2}'>{esc(line)}</tspan>")
            parts.append('</text>')
        parts.append('</g>')
    index = 0
    for i, block in enumerate(blocks[:80]):
        if not isinstance(block, dict) or block.get('scope') == 'item': continue
        key = str(block.get('holder_key') or '')
        draw(block, saved_by_key.get(key, saved_blocks[i] if i < len(saved_blocks) and isinstance(saved_blocks[i], dict) else block), index)
        index += 1
    item_blocks = [block for block in blocks if isinstance(block, dict) and block.get('scope') == 'item']
    items = data.get('items', []) if isinstance(data.get('items'), list) else []
    assignments = data.get('item_assignments') if isinstance(data.get('item_assignments'), list) else template.get('item_assignments', [])
    try: count = max(1, min(30, int(template.get('item_count', 1))))
    except (TypeError, ValueError): count = 1
    for item_i in range(count):
        item = items[item_i] if item_i < len(items) and isinstance(items[item_i], dict) else {}
        assigned = set(assignments[item_i]) if item_i < len(assignments) and isinstance(assignments[item_i], list) else {str(b.get('holder_key') or '') for b in item_blocks}
        for block in item_blocks:
            if str(block.get('holder_key') or '') in assigned:
                draw(block, item.get(str(block.get('holder_key') or ''), {}), index)
                index += 1
    svg = f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' viewBox='0 0 {width} {height}'>{''.join(parts)}</svg>"
    return 'data:image/svg+xml;base64,' + base64.b64encode(svg.encode()).decode('ascii')


def store_menu_artwork(menu, template, administrator_id, menu_id):
    """Upload a fresh SVG artwork object and return its URL and inline DB copy."""
    orientation = template.get('canvas_orientation', 'portrait')
    width, height = (1200, 1920) if orientation == 'portrait' else (1920, 1200)
    preview = render_menu_svg(menu, template, width, height)
    try:
        svg_bytes = base64.b64decode(preview.split(',', 1)[1])
        with tempfile.NamedTemporaryFile(suffix='.svg', delete=False) as temp:
            temp.write(svg_bytes)
            temp_path = temp.name
        key = f"menus/{administrator_id}/{menu_id}/{uuid.uuid4().hex}.svg"
        return {'url': upload_file_to_s3(temp_path, object_name=key, content_type='image/svg+xml'), 'data': preview}
    except Exception as exc:
        print(f"[Menu artwork] Could not store artwork: {exc}")
        return None
    finally:
        if 'temp_path' in locals():
            try: os.unlink(temp_path)
            except OSError: pass


def menu_template_for(menu):
    template_id = menu.get('template_id')
    try: template_id = ObjectId(template_id) if isinstance(template_id, str) and len(template_id) == 24 else template_id
    except Exception: pass
    return menu_templates_collection.find_one({'_id': template_id})


def cleanup_previous_menu_artwork(url, administrator_id, menu_id, new_url=None):
    if (url and url != new_url and isinstance(url, str)
            and f'/menus/{administrator_id}/{menu_id}/' in url):
        delete_file_from_s3(url)


def render_menu_html(menu, template):
    data = menu.get('data', {})
    if template.get('custom_layout'):
        template_blocks = template.get('blocks', []) if isinstance(template.get('blocks'), list) else []
        saved_blocks = data.get('blocks', []) if isinstance(data.get('blocks'), list) else []
        saved_by_key = {str(block.get('holder_key')): block for block in saved_blocks if isinstance(block, dict) and block.get('holder_key')}
        pieces = []

        def block_html(block, value_block, left, top, width, height):
            x = max(0, min(100, float(block.get('x', 0) or 0)))
            y = max(0, min(100, float(block.get('y', 0) or 0)))
            w = max(1, min(100, float(block.get('width', 20) or 20)))
            h = max(1, min(100, float(block.get('height', 15) or 15)))
            style = f'left:{left + width*x/100}%;top:{top + height*y/100}%;width:{width*w/100}%;height:{height*h/100}%;position:absolute;overflow:hidden;'
            value_block = value_block if isinstance(value_block, dict) else {}
            if block.get('type') == 'image':
                src = str(value_block.get('image_url', '')).strip()
                if src.startswith(('https://', 'http://', 'data:image/')):
                    fit = 'contain' if block.get('crop') == 'contain' else 'cover'
                    return f"<div style='{style}'><img src='{html_lib.escape(src, quote=True)}' style='width:100%;height:100%;object-fit:{fit}'/></div>"
                return ''
            font = block.get('font_family') if block.get('font_family') in ('Arial', 'Georgia', 'Verdana', 'Tahoma') else 'Arial'
            try: size = max(8, min(120, int(block.get('font_size', 24) or 24)))
            except (ValueError, TypeError): size = 24
            weight = '700' if block.get('bold') else '400'
            color = str(block.get('color', '#171717'))
            if not re.fullmatch(r'#[0-9a-fA-F]{6}', color): color = '#171717'
            value = html_lib.escape(str(value_block.get('text', '')), quote=True)
            return f"<div style='{style}font:{weight} {size}px {font};color:{color};white-space:pre-wrap'>{value}</div>"

        for i, block in enumerate(template_blocks[:80]):
            if not isinstance(block, dict) or block.get('scope') == 'item':
                continue
            key = str(block.get('holder_key') or '')
            value = saved_by_key.get(key, saved_blocks[i] if i < len(saved_blocks) and isinstance(saved_blocks[i], dict) else block)
            pieces.append(block_html(block, value, 0, 0, 100, 100))

        item_blocks = [block for block in template_blocks if isinstance(block, dict) and block.get('scope') == 'item']
        item_values = data.get('items', []) if isinstance(data.get('items'), list) else []
        try: count = max(1, min(30, int(template.get('item_count', 1))))
        except (ValueError, TypeError): count = 1
        assignments = data.get('item_assignments') if isinstance(data.get('item_assignments'), list) else template.get('item_assignments', [])
        for item_index in range(count):
            item = item_values[item_index] if item_index < len(item_values) and isinstance(item_values[item_index], dict) else {}
            assigned_keys = set(assignments[item_index]) if item_index < len(assignments) and isinstance(assignments[item_index], list) else {str(block.get('holder_key') or '') for block in item_blocks}
            for block in item_blocks:
                if str(block.get('holder_key') or '') not in assigned_keys:
                    continue
                pieces.append(block_html(block, item.get(str(block.get('holder_key') or ''), {}), 0, 0, 100, 100))

        bg = str(data.get('background', template.get('background', '#fff')))
        if not re.fullmatch(r'#[0-9a-fA-F]{6}', bg): bg = '#ffffff'
        bg_image = str(data.get('background_image') or template.get('background_image', '')).strip()
        bg_style = f'background:{bg};'
        if bg_image.startswith(('https://', 'http://', 'data:image/')):
            bg_style += f"background-image:url('{html_lib.escape(bg_image, quote=True)}');background-size:cover;background-position:center;"
        return f"<div style='position:relative;width:100%;height:100%;min-height:100%;overflow:hidden;{bg_style}'>{''.join(pieces)}</div>"
    def text(key, default=''):
        component = next((item for item in template.get('components', []) if item.get('key') == key), {})
        return html_lib.escape(str(data.get(key, component.get('default', default)) or ''), quote=True)
    items_html = []
    for item in data.get('items', []) if isinstance(data.get('items'), list) else []:
        if not isinstance(item, dict):
            continue
        image_url = str(item.get('image_url', '')).strip()
        image_html = ''
        if image_url.startswith(('https://', 'http://', 'data:image/')):
            image_html = f"<img src='{html_lib.escape(image_url, quote=True)}' alt='' />"
        items_html.append(
            f"<article>{image_html}<div class='item-copy'><strong>{html_lib.escape(str(item.get('name', '')), quote=True)}</strong>"
            f"<span class='price'>{html_lib.escape(str(item.get('price', '')), quote=True)}</span>"
            f"<p>{html_lib.escape(str(item.get('description', '')), quote=True)}</p></div></article>"
        )
    rendered = template.get('html_template', MENU_TEMPLATE_SEEDS[0]['html_template'])
    for key in ('eyebrow', 'title', 'subtitle', 'footer'):
        rendered = rendered.replace('{{' + key + '}}', text(key, menu.get('title', '')))
    return rendered.replace('{{items}}', ''.join(items_html))


class Administrator(APIView):
    
    def post(self, request):
        if 'service' in request.data:
            service = request.data['service']
            # print("Incoming service request:", service, "data:", request.data)
        else:
            return Response({'status_code': 400, 'status_text': 'Required service'}, status=400)

        if service == 'login':
            username = (request.data.get('email') or request.data.get('username') or '').lower().strip()
            password = request.data.get('password')
            administrator = administrators_collection.find_one({"email_id": username})
            if administrator and not check_password(str(password or ''), administrator.get('password_hash', '')):
                administrator = None
            # Existing installations used plaintext passwords; accept them once and migrate on successful login.
            if not administrator:
                administrator = administrators_collection.find_one({"$or": [{"email_id": username}, {"username": username}], "password": password})
                if administrator:
                    migrated_hash = make_password(str(password))
                    administrators_collection.update_one({'_id': administrator['_id']}, {'$set': {'password_hash': migrated_hash}, '$unset': {'password': ''}})
                    users_collection.update_one({'_id': administrator['_id']}, {'$set': {'password_hash': migrated_hash}, '$unset': {'password': ''}})
            if administrator:
                login_key = administrator.get('login_key') or secrets.token_urlsafe(32)
                administrators_collection.update_one({'_id': administrator['_id']}, {'$set': {'login_key': login_key}})
                return Response({'status_text': 'ok', 'status_code': 200, 'login_key': login_key,
                                 'plan_active': plan_is_active(administrator)})
            return Response({'status_text': 'UnAuthorised', 'status_code': 401}, status=401)

        elif service == 'request_otp':
            email = (request.data.get('email') or '').lower().strip()
            purpose = request.data.get('purpose')
            if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email) or purpose not in ('register', 'reset_password'):
                return Response({'status_text': 'A valid email and OTP purpose are required.', 'status_code': 400}, status=400)
            existing = administrators_collection.find_one({'email_id': email})
            if purpose == 'register' and existing:
                return Response({'status_text': 'Email already exists. Please sign in.', 'status_code': 409}, status=409)
            if purpose == 'reset_password' and not existing:
                return Response({'status_text': 'If the account exists, a verification code has been sent.', 'status_code': 200})
            try:
                send_email_otp(email, purpose)
            except Exception:
                return Response({'status_text': 'Email delivery is unavailable. Configure the server email backend and try again.', 'status_code': 503}, status=503)
            return Response({'status_text': 'Verification code sent.', 'status_code': 200})

        elif service == 'reset_password':
            email = (request.data.get('email') or '').lower().strip()
            password = request.data.get('password') or ''
            if len(password) < 8 or not consume_email_otp(email, request.data.get('otp'), 'reset_password'):
                return Response({'status_text': 'Invalid or expired code, or password must be at least 8 characters.', 'status_code': 400}, status=400)
            password_hash = make_password(password)
            result = administrators_collection.update_one({'email_id': email}, {'$set': {'password_hash': password_hash}, '$unset': {'password': ''}})
            if not result.matched_count:
                return Response({'status_text': 'Account not found.', 'status_code': 404}, status=404)
            users_collection.update_one({'email_id': email}, {'$set': {'password_hash': password_hash}, '$unset': {'password': ''}})
            return Response({'status_text': 'Password updated. Please sign in.', 'status_code': 200})
        
        elif service == 'register':
            required_data = ['username', 'password', 'email', 'otp']
            if not all(field in request.data for field in required_data):
                return Response({"status_text": "Required keys: username, password, email"}, status=400)
            username = request.data['username'].strip()
            password = request.data['password']
            email_id = request.data['email'].lower().strip()
            if len(password) < 8 or not consume_email_otp(email_id, request.data.get('otp'), 'register'):
                return Response({'status_text': 'A valid, unexpired email verification code and password of at least 8 characters are required.', 'status_code': 400}, status=400)
            profile_image_url = request.data.get('profile_image_url', '')
            contact = request.data.get('contact', '')
            address_line_1 = request.data.get('address_line_1', '')
            address_line_2 = request.data.get('address_line_2', '')
            address_line_3 = request.data.get('address_line_3', '')

            if administrators_collection.find_one({'email_id': email_id}):
                return Response({'status': 'Email Already Exist Please Login', 'status_code': 400}, status=400)
            if administrators_collection.find_one({'username': username}):
                return Response({'status': 'Username Already taken', 'status_code': 400}, status=400)

            administrator_data = {
                "username": username,
                "password_hash": make_password(password),
                "email_id": email_id,
                "profile_image_url": profile_image_url,
                "contact": contact,
                "address_line_1": address_line_1,
                "address_line_2": address_line_2,
                "address_line_3": address_line_3,
                "login_key": secrets.token_urlsafe(32),
                "created_at": datetime.now(timezone.utc),
                "subscription": {"status": "inactive"},
            }
            inserted = administrators_collection.insert_one(administrator_data)
            administrator_data['_id'] = inserted.inserted_id
            users_collection.delete_one({'email_id': email_id})
            users_collection.replace_one({'_id': inserted.inserted_id}, administrator_data, upsert=True)
            return Response({'status_text': 'Registered Successfully', 'status_code': 200, 'login_key': administrator_data['login_key'], 'plan_active': False})

        elif service == 'check_plan':
            user = get_administrator(request.data.get('login_key'))
            if not user:
                return Response({'status_text': 'Please sign in.', 'status_code': 401}, status=401)
            return Response({'status_code': 200, 'plan_active': plan_is_active(user), 'subscription': user.get('subscription', {})})

        elif service == 'GetMenuTemplates':
            ensure_menu_templates()
            administrator = get_administrator(request.data.get('login_key'))
            query = {'$or': [{'administrator_id': administrator['_id']}, {'administrator_id': {'$exists': False}}]} if administrator else {'administrator_id': {'$exists': False}}
            templates = [serialize_doc(dict(item)) for item in menu_templates_collection.find(query)]
            return Response({'status_code': 200, 'data': templates})

        elif service == 'GetMenus':
            administrator = get_administrator(request.data.get('login_key'))
            if not administrator:
                return Response({'status_code': 401, 'status_text': 'Please sign in.'}, status=401)
            menus = []
            for item in menus_collection.find({'administrator_id': administrator['_id']}).sort('updated_at', -1):
                item = dict(item)
                item.setdefault('is_active', True)
                item.setdefault('mirror_ids', [])
                if not item.get('image_data') or item.get('artwork_version', 0) < MENU_ARTWORK_VERSION:
                    old_image_url = item.get('artwork_s3_url') or item.get('image_url')
                    template = menu_template_for(item)
                    if template:
                        artwork = store_menu_artwork(item, template, administrator['_id'], item['_id'])
                        if artwork:
                            item['image_data'] = artwork['data']
                            item['artwork_s3_url'] = artwork['url']
                            item['image_url'] = artwork['data']
                            item['artwork_version'] = MENU_ARTWORK_VERSION
                            menus_collection.update_one({'_id': item['_id']}, {'$set': {'image_data': artwork['data'], 'image_url': artwork['data'], 'artwork_s3_url': artwork['url'], 'artwork_version': MENU_ARTWORK_VERSION}})
                            cleanup_previous_menu_artwork(old_image_url, administrator['_id'], item['_id'], artwork['url'])
                menus.append(serialize_doc(item))
            return Response({'status_code': 200, 'data': menus})

        elif service == 'GetMenuShareLink':
            administrator = get_administrator(request.data.get('login_key'))
            mirror_id = request.data.get('mirror_id')
            try:
                mirror_object_id = ObjectId(mirror_id)
            except Exception:
                return Response({'status_code': 400, 'status_text': 'Select a valid mirror.'}, status=400)
            owner_id = administrator.get('_id') if administrator else None
            mirror = mirror_collection.find_one({'_id': mirror_object_id, 'administrator_id': owner_id}) if owner_id else None
            if not mirror:
                return Response({'status_code': 404, 'status_text': 'Mirror not found.'}, status=404)
            token = mirror.get('menu_share_token')
            if not token:
                token = secrets.token_urlsafe(24)
                mirror_collection.update_one({'_id': mirror['_id']}, {'$set': {'menu_share_token': token}})
            return Response({'status_code': 200, 'share_token': token, 'mirror_name': mirror.get('mirror_name', 'Menu')})

        elif service == 'GetPublicMirrorMenus':
            token = str(request.data.get('share_token') or '')
            mirror = mirror_collection.find_one({'menu_share_token': token}) if token else None
            if not mirror:
                return Response({'status_code': 404, 'status_text': 'This menu QR is unavailable.'}, status=404)
            mirror_id = str(mirror['_id'])
            admin_id = mirror.get('administrator_id')
            public_menus = []
            ensure_menu_templates()
            for menu in menus_collection.find({
                'administrator_id': admin_id,
                'is_active': {'$ne': False},
                'mirror_ids': {'$in': [mirror_id, mirror['_id']]},
            }).sort('updated_at', -1):
                template = menu_template_for(menu)
                if not template:
                    continue
                if not menu.get('image_data') or menu.get('artwork_version', 0) < MENU_ARTWORK_VERSION:
                    old_image_url = menu.get('artwork_s3_url') or menu.get('image_url')
                    artwork = store_menu_artwork(menu, template, admin_id, menu['_id'])
                    if artwork:
                        menu['image_data'] = artwork['data']
                        menu['image_url'] = artwork['data']
                        menu['artwork_s3_url'] = artwork['url']
                        menu['artwork_version'] = MENU_ARTWORK_VERSION
                        menus_collection.update_one({'_id': menu['_id']}, {'$set': {'image_data': artwork['data'], 'image_url': artwork['data'], 'artwork_s3_url': artwork['url'], 'artwork_version': MENU_ARTWORK_VERSION}})
                        cleanup_previous_menu_artwork(old_image_url, admin_id, menu['_id'], artwork['url'])
                public_menus.append({'_id': str(menu['_id']), 'title': menu.get('title', 'Menu'), 'image_url': menu.get('image_data') or menu.get('image_url'),
                                     'html': render_menu_html(menu, template), 'layout': template.get('layout', 'classic'),
                                     'custom_layout': bool(template.get('custom_layout')), 'orientation': template.get('canvas_orientation', 'portrait'),
                                     'background': menu.get('data', {}).get('background') if re.fullmatch(r'#[0-9a-fA-F]{6}', str(menu.get('data', {}).get('background', ''))) else template.get('background', '#fff'),
                                     'accent': menu.get('data', {}).get('accent') if re.fullmatch(r'#[0-9a-fA-F]{6}', str(menu.get('data', {}).get('accent', ''))) else template.get('accent', '#111')})
            return Response({'status_code': 200, 'mirror_name': mirror.get('mirror_name', 'Menu'), 'data': public_menus})

        elif service == 'SetMenuMirrors':
            administrator = get_administrator(request.data.get('login_key'))
            if not administrator:
                return Response({'status_code': 401, 'status_text': 'Please sign in.'}, status=401)
            try:
                menu_id = ObjectId(request.data.get('menu_id'))
                mirror_id = ObjectId(request.data.get('mirror_id'))
            except Exception:
                return Response({'status_code': 400, 'status_text': 'Select a valid menu and mirror.'}, status=400)
            menu = menus_collection.find_one({'_id': menu_id, 'administrator_id': administrator['_id']})
            mirror = mirror_collection.find_one({'_id': mirror_id, 'administrator_id': administrator['_id']})
            if not menu or not mirror:
                return Response({'status_code': 404, 'status_text': 'Menu or mirror not found.'}, status=404)
            ids = [str(value) for value in menu.get('mirror_ids', [])]
            if request.data.get('assigned') and str(mirror_id) not in ids:
                ids.append(str(mirror_id))
            elif not request.data.get('assigned'):
                ids = [value for value in ids if value != str(mirror_id)]
            menus_collection.update_one({'_id': menu_id}, {'$set': {'mirror_ids': ids, 'updated_at': datetime.now(timezone.utc)}})
            return Response({'status_code': 200, 'mirror_ids': ids})

        elif service == 'SetMenuActive':
            administrator = get_administrator(request.data.get('login_key'))
            if not administrator:
                return Response({'status_code': 401, 'status_text': 'Please sign in.'}, status=401)
            try:
                menu_id = ObjectId(request.data.get('menu_id'))
            except Exception:
                return Response({'status_code': 400, 'status_text': 'Invalid menu id.'}, status=400)
            result = menus_collection.update_one({'_id': menu_id, 'administrator_id': administrator['_id']}, {'$set': {'is_active': bool(request.data.get('is_active')), 'updated_at': datetime.now(timezone.utc)}})
            if not result.matched_count:
                return Response({'status_code': 404, 'status_text': 'Menu not found.'}, status=404)
            return Response({'status_code': 200, 'is_active': bool(request.data.get('is_active'))})

        elif service == 'SaveMenuTemplate':
            administrator = get_administrator(request.data.get('login_key'))
            if not administrator:
                return Response({'status_code': 401, 'status_text': 'Please sign in.'}, status=401)
            payload = request.data.get('template') if isinstance(request.data.get('template'), dict) else {}
            name = str(payload.get('name') or '').strip()
            blocks = payload.get('blocks') if isinstance(payload.get('blocks'), list) else []
            if not name:
                return Response({'status_code': 400, 'status_text': 'Enter a template name.'}, status=400)
            if len(blocks) > 80 or any(not isinstance(block, dict) or block.get('type') not in ('text', 'image') for block in blocks):
                return Response({'status_code': 400, 'status_text': 'Template holders are invalid.'}, status=400)
            try:
                item_count = max(1, min(30, int(payload.get('item_count', 1))))
            except (TypeError, ValueError):
                item_count = 1
            item_area = payload.get('item_area') if isinstance(payload.get('item_area'), dict) else {'x': 5, 'y': 34, 'width': 90, 'height': 60, 'columns': 2}
            try:
                columns = max(1, min(3, int(item_area.get('columns', 2))))
            except (TypeError, ValueError):
                columns = 2
            item_area = {key: max(0, min(100, float(item_area.get(key, default)))) for key, default in [('x', 5), ('y', 34), ('width', 90), ('height', 60)]}
            item_area['columns'] = columns
            valid_holder_keys = {str(block.get('holder_key')) for block in blocks if block.get('scope') == 'item' and block.get('holder_key')}
            raw_assignments = payload.get('item_assignments') if isinstance(payload.get('item_assignments'), list) else []
            item_assignments = [[str(key) for key in raw_assignments[i] if str(key) in valid_holder_keys] if i < len(raw_assignments) and isinstance(raw_assignments[i], list) else [] for i in range(item_count)]
            orientation = payload.get('canvas_orientation') if payload.get('canvas_orientation') in ('portrait', 'landscape') else 'landscape'
            template_doc = {'name': name, 'description': 'Custom menu layout', 'background': payload.get('background', '#ffffff'), 'background_image': payload.get('background_image', ''), 'custom_layout': True, 'layout': 'custom', 'canvas_orientation': orientation, 'blocks': blocks, 'item_count': item_count, 'item_area': item_area, 'item_assignments': item_assignments, 'components': [], 'administrator_id': administrator['_id'], 'updated_at': datetime.now(timezone.utc)}
            template_id = payload.get('_id')
            if template_id:
                try:
                    object_id = ObjectId(template_id)
                except Exception:
                    return Response({'status_code': 400, 'status_text': 'Invalid template id.'}, status=400)
                result = menu_templates_collection.update_one({'_id': object_id, 'administrator_id': administrator['_id'], 'custom_layout': True}, {'$set': template_doc})
                if not result.matched_count:
                    return Response({'status_code': 404, 'status_text': 'Custom template not found.'}, status=404)
                saved_id = object_id
            else:
                template_doc['created_at'] = datetime.now(timezone.utc)
                saved_id = menu_templates_collection.insert_one(template_doc).inserted_id
            return Response({'status_code': 200, 'template_id': str(saved_id), 'status_text': 'Template saved.'})

        elif service == 'SaveMenu':
            administrator = get_administrator(request.data.get('login_key'))
            if not administrator:
                return Response({'status_code': 401, 'status_text': 'Please sign in.'}, status=401)
            template_id = str(request.data.get('template_id') or '')
            menu_data = request.data.get('data') if isinstance(request.data.get('data'), dict) else {}
            custom_template = request.data.get('custom_template') if isinstance(request.data.get('custom_template'), dict) else None
            if custom_template:
                template_id = str(custom_template.get('_id') or '')
                template_doc = {k: v for k, v in custom_template.items() if k != '_id'}
                template_doc.update({'administrator_id': administrator['_id'], 'is_custom': True, 'layout': 'custom'})
                if template_id:
                    try:
                        template_oid = ObjectId(template_id)
                        existing = menu_templates_collection.find_one({'_id': template_oid, 'administrator_id': administrator['_id']})
                        if existing:
                            menu_templates_collection.update_one({'_id': template_oid}, {'$set': template_doc})
                            template_id = str(template_oid)
                        else:
                            template_id = ''
                    except Exception:
                        template_id = ''
                if not template_id:
                    template_id = str(menu_templates_collection.insert_one(template_doc).inserted_id)
            menu_title = str(menu_data.get('title') or request.data.get('title') or '').strip()
            if not template_id or not menu_title:
                return Response({'status_code': 400, 'status_text': 'Select a template and enter a menu title.'}, status=400)
            ensure_menu_templates()
            try:
                template_key = ObjectId(template_id) if len(template_id) == 24 else template_id
            except Exception:
                template_key = template_id
            template = menu_templates_collection.find_one({'_id': template_key, '$or': [{'administrator_id': administrator['_id']}, {'administrator_id': {'$exists': False}}]})
            if not template:
                return Response({'status_code': 404, 'status_text': 'Menu template not found.'}, status=404)
            now = datetime.now(timezone.utc)
            menu_id = request.data.get('menu_id')
            if menu_id:
                try:
                    object_id = ObjectId(menu_id)
                except Exception:
                    return Response({'status_code': 400, 'status_text': 'Invalid menu id.'}, status=400)
                existing_menu = menus_collection.find_one({'_id': object_id, 'administrator_id': administrator['_id']})
                if not existing_menu:
                    return Response({'status_code': 404, 'status_text': 'Menu not found.'}, status=404)
                saved_id = object_id
                old_image_url = existing_menu.get('artwork_s3_url') or existing_menu.get('image_url')
            else:
                saved_id = ObjectId()
                old_image_url = None
            artwork = store_menu_artwork({'_id': saved_id, 'title': menu_title, 'data': menu_data}, template, administrator['_id'], saved_id)
            if not artwork:
                return Response({'status_code': 503, 'status_text': 'Could not generate menu artwork.'}, status=503)
            new_image_url = artwork['data']
            if menu_id:
                result = menus_collection.update_one(
                    {'_id': object_id, 'administrator_id': administrator['_id']},
                    {'$set': {'template_id': template_id, 'title': menu_title, 'data': menu_data, 'image_url': new_image_url, 'image_data': artwork['data'], 'artwork_s3_url': artwork['url'], 'artwork_version': MENU_ARTWORK_VERSION, 'updated_at': now}}
                )
                if not result.matched_count:
                    return Response({'status_code': 404, 'status_text': 'Menu not found.'}, status=404)
                cleanup_previous_menu_artwork(old_image_url, administrator['_id'], saved_id, artwork['url'])
            else:
                menus_collection.insert_one({
                    '_id': saved_id,
                    'administrator_id': administrator['_id'], 'template_id': template_id,
                    'title': menu_title, 'data': menu_data, 'image_url': new_image_url, 'image_data': artwork['data'], 'artwork_s3_url': artwork['url'], 'artwork_version': MENU_ARTWORK_VERSION, 'is_active': True, 'mirror_ids': [], 'created_at': now, 'updated_at': now,
                })
            return Response({'status_code': 200, 'menu_id': str(saved_id), 'image_url': new_image_url, 'status_text': 'Menu saved.'})

        elif service == 'DeleteMenu':
            administrator = get_administrator(request.data.get('login_key'))
            try:
                menu_id = ObjectId(request.data.get('menu_id'))
            except Exception:
                return Response({'status_code': 400, 'status_text': 'Invalid menu id.'}, status=400)
            result = menus_collection.delete_one({'_id': menu_id, 'administrator_id': administrator['_id'] if administrator else None})
            if not result.deleted_count:
                return Response({'status_code': 404, 'status_text': 'Menu not found.'}, status=404)
            return Response({'status_code': 200, 'status_text': 'Menu deleted.'})

        elif service == 'DeleteMenuTemplate':
            administrator = get_administrator(request.data.get('login_key'))
            if not administrator:
                return Response({'status_code': 401, 'status_text': 'Please sign in.'}, status=401)
            try:
                template_id = ObjectId(request.data.get('template_id'))
            except Exception:
                return Response({'status_code': 400, 'status_text': 'Invalid template id.'}, status=400)
            template = menu_templates_collection.find_one({'_id': template_id, 'administrator_id': administrator['_id'], 'custom_layout': True})
            if not template:
                return Response({'status_code': 404, 'status_text': 'Custom template not found.'}, status=404)
            if menus_collection.find_one({'administrator_id': administrator['_id'], 'template_id': {'$in': [str(template_id), template_id]}}):
                return Response({'status_code': 409, 'status_text': 'This template is used by a saved menu. Delete or update that menu before removing the template.'}, status=409)
            menu_templates_collection.delete_one({'_id': template_id, 'administrator_id': administrator['_id'], 'custom_layout': True})
            return Response({'status_code': 200, 'status_text': 'Custom template deleted.'})

        elif service == 'RenderMenuPreview':
            administrator = get_administrator(request.data.get('login_key'))
            try:
                menu_id = ObjectId(request.data.get('menu_id'))
            except Exception:
                return Response({'status_code': 400, 'status_text': 'Invalid menu id.'}, status=400)
            menu = menus_collection.find_one({'_id': menu_id, 'administrator_id': administrator['_id'] if administrator else None})
            if not menu:
                return Response({'status_code': 404, 'status_text': 'Menu not found.'}, status=404)
            tid = menu.get('template_id')
            try: tid = ObjectId(tid) if isinstance(tid, str) and len(tid) == 24 else tid
            except Exception: pass
            template = menu_templates_collection.find_one({'_id': tid})
            if not template:
                return Response({'status_code': 404, 'status_text': 'Menu template not found.'}, status=404)
            try:
                width = min(3840, max(320, int(request.data.get('width', 1920))))
                height = min(2160, max(320, int(request.data.get('height', 1080))))
            except (ValueError, TypeError):
                width, height = 1920, 1080
            return Response({
                'status_code': 200,
                'preview_url': menu.get('image_data') or menu.get('image_url') or render_menu_svg(menu, template, width, height),
                'width': width, 'height': height,
            })

        elif service == 'AddContent':      
            login_key = request.data.get('login_key')
            administrator = get_administrator(login_key)
            if not administrator:
                return Response({'status_text': 'Unauthorized', 'status_code': 401}, status=401)
            
            administrator_id = administrator.get('_id')
            content = request.data.get('content')
            content_title = request.data.get('content_title')
            content_description = request.data.get('content_description', '')
            raw_ext = str(request.data.get('file_extention', 'jpg')).lower().strip('.')
            mirror_id = request.data.get('mirror_id')
            duration = request.data.get('duration')
            if not content or not content_title:
                return Response({'status_text': 'content and content_title required', 'status_code': 400}, status=400)

            # Normalize file extension
            if raw_ext in ['quicktime', 'mov']:
                file_extention = 'mov'
            elif raw_ext in ['mp4', 'video/mp4', 'm4v']:
                file_extention = 'mp4'
            elif raw_ext in ['webm', 'video/webm']:
                file_extention = 'webm'
            elif raw_ext in ['jpeg', 'jpg', 'image/jpeg']:
                file_extention = 'jpeg'
            elif raw_ext in ['png', 'image/png']:
                file_extention = 'png'
            elif raw_ext in ['webp', 'image/webp']:
                file_extention = 'webp'
            else:
                file_extention = raw_ext

            is_video = file_extention in ['mp4', 'mov', 'webm', 'm4v', 'quicktime', 'mkv', 'avi'] or (content and 'video' in content[:50])

            file_data = content.split(',')[1] if ',' in content else content
            decoded_file_data = base64.b64decode(file_data)
            os.makedirs('Contents', exist_ok=True)
            
            # Clean safe filename for disk and S3
            safe_title = "".join(c for c in content_title if c.isalnum() or c in ('-', '_')).rstrip()
            if not safe_title:
                safe_title = "content"
            file_name = f"{safe_title}_{str(administrator_id)}.{file_extention}"
            file_path = os.path.join('Contents', file_name)
            with open(file_path, 'wb') as file:
                file.write(decoded_file_data)
            
            # Upload to AWS S3 with explicit ContentType
            content_type = None
            if is_video:
                if file_extention == 'mp4':
                    content_type = 'video/mp4'
                elif file_extention == 'mov':
                    content_type = 'video/quicktime'
                elif file_extention == 'webm':
                    content_type = 'video/webm'
                else:
                    content_type = f'video/{file_extention}'

            s3_url = upload_file_to_s3(file_path, object_name=f"contents/{file_name}", content_type=content_type)
            content_url = s3_url if s3_url else f"https://mirrorlink.bigburry.com/Contents/{file_name}"

            # Find mirror if mirror_id provided
            mirror = None
            if mirror_id:
                try:
                    mirror = mirror_collection.find_one({'_id': ObjectId(mirror_id)})
                except Exception:
                    mirror = mirror_collection.find_one({'_id': mirror_id})
                
                if mirror:
                    try:
                        mirror_w = int(mirror.get('mirror_width', 1920))
                        mirror_h = int(mirror.get('mirror_height', 1080))
                        req_w = request.data.get('width')
                        req_h = request.data.get('height')
                        # Strict resolution matching is required for images; videos are auto-scaled and play natively
                        if not is_video and req_w is not None and req_h is not None:
                            if int(req_w) != mirror_w or int(req_h) != mirror_h:
                                return Response({
                                    'status_text': f'Media dimensions ({req_w} × {req_h} px) do not match mirror resolution ({mirror_w} × {mirror_h} px).',
                                    'status_code': 400
                                }, status=400)
                    except (ValueError, TypeError):
                        pass

            new_content = {
                "administrator_id": ObjectId(administrator_id),
                "mirror_id": mirror['_id'] if mirror else (ObjectId(mirror_id) if mirror_id else None),
                "content_title": content_title,
                "content_description": content_description,
                "content_url": content_url,
                "content_format": "mp4" if (is_video and file_extention not in ['webm', 'mov']) else file_extention,
                "is_active": True,
                "order": len(mirror.get('contents', [])) + 1 if mirror else 1
            }
            res = content_collection.insert_one(new_content)
            new_content_id = res.inserted_id

            if mirror:
                contents = list(mirror.get('contents', []))
                contents.append(new_content_id)
                update_fields = {
                    'contents': contents,
                    'last_refresh_timestamp': time.time()
                }
                if duration:
                    update_fields[f'content_durations.{str(new_content_id)}'] = max(1, int(duration))
                
                mirror_collection.update_one({'_id': mirror['_id']}, {'$set': update_fields})

                # Broadcast reload event to active viewers and Virtual Screen
                try:
                    from channels.layers import get_channel_layer
                    from asgiref.sync import async_to_sync
                    channel_layer = get_channel_layer()
                    if channel_layer:
                        msg = {
                            'type': 'update.mirror',
                            'data': {
                                'service': 'ReloadContents',
                                'mirror_id': str(mirror['_id']),
                                'timestamp': time.time()
                            }
                        }
                        async_to_sync(channel_layer.group_send)("mirror_group", msg)
                        async_to_sync(channel_layer.group_send)(f"mirror_{str(mirror['_id'])}", msg)
                except Exception as e:
                    print(f"Error broadcasting ReloadContents on AddContent: {e}")

            return Response({
                'status_text': 'ok',
                'status_code': 200,
                'content_url': content_url,
                'content_id': str(new_content_id)
            })
                                 
        elif service == 'GetMyUnassignedMirrors':
            login_key = request.data.get('login_key')
            administrator = get_administrator(login_key)
            if not administrator:
                return Response({'status_text': 'ok', 'status_code': 200, 'data': []})
            
            administrator_id = administrator.get('_id')
            MyMirrors = mirror_collection.find({
                'administrator_id': {'$in': [administrator_id, str(administrator_id), ObjectId(administrator_id)]},
                '$or': [{'site_id': None}, {'site_id': {'$exists': False}}]
            })
            MyMirrors_list = [serialize_doc(dict(mirror)) for mirror in MyMirrors]
            return Response({'status_text': 'ok', 'status_code': 200, 'data': MyMirrors_list})

        elif service == 'GetMyMirrors':
            login_key = request.data.get('login_key')
            administrator = get_administrator(login_key)
            if not administrator:
                return Response({'status_text': 'ok', 'status_code': 200, 'data': []})
            
            administrator_id = administrator.get('_id')
            MyMirrors = mirror_collection.find({
                'administrator_id': {'$in': [administrator_id, str(administrator_id), ObjectId(administrator_id)]}
            })
            MyMirrors_list = [serialize_doc(dict(mirror)) for mirror in MyMirrors]
            return Response({'status_text': 'Mirrors fetched Successfully', 'status_code': 200, 'data': MyMirrors_list})

        elif service == 'GetMySiteMirrors':
            login_key = request.data.get('login_key')
            site_id = request.data.get('site_id')
            administrator = get_administrator(login_key)
            if not administrator:
                return Response({'status_text': 'ok', 'status_code': 200, 'data': []})
            
            administrator_id = administrator.get('_id')
            query = {
                'administrator_id': {'$in': [administrator_id, str(administrator_id), ObjectId(administrator_id)]}
            }
            if site_id:
                try:
                    query['site_id'] = {'$in': [site_id, ObjectId(site_id)]}
                except Exception:
                    query['site_id'] = site_id

            MyMirrors = mirror_collection.find(query)
            MyMirrors_list = [serialize_doc(dict(mirror)) for mirror in MyMirrors]
            return Response({'status_text': 'Mirrors fetched Successfully', 'status_code': 200, 'data': MyMirrors_list})

        elif service == 'AssignMirrorToSite':
            login_key = request.data.get('login_key')
            site_id = request.data.get('site_id')
            mirrors = request.data.get('mirrors', [])
            administrator = get_administrator(login_key)
            if administrator and site_id:
                administrator_id = administrator.get('_id')
                try:
                    site_object_id = ObjectId(site_id)
                except Exception:
                    site_object_id = site_id
                site = sites_collection.find_one({
                    '_id': site_object_id,
                    'administrator_id': {'$in': administrator_ids(administrator_id)}
                })
                if not site:
                    return Response({'status_text': 'Site not found in your account.', 'status_code': 404}, status=404)
                owned_query = {'administrator_id': {'$in': administrator_ids(administrator_id)}}
                target_mirrors = []
                for mirror in mirrors:
                    mirror_id = mirror.get('_id') if isinstance(mirror, dict) else mirror
                    try:
                        mirror_object_id = ObjectId(mirror_id)
                    except Exception:
                        mirror_object_id = mirror_id
                    found = mirror_collection.find_one({'_id': mirror_object_id, **owned_query})
                    if found and str(found.get('site_id')) != str(site_object_id):
                        target_mirrors.append(found)
                assigned_count = mirror_collection.count_documents({
                    **owned_query,
                    'site_id': {'$in': [site_object_id, str(site_object_id)]}
                })
                if assigned_count + len(target_mirrors) > PLAN['mirrors_per_site']:
                    return Response({'status_text': 'This plan allows up to 50 mirrors per site.', 'status_code': 400}, status=400)
                for mirror in target_mirrors:
                    mirror_collection.update_one({'_id': mirror['_id']}, {'$set': {'site_id': site_object_id}})
                return Response({'status_text': 'Mirror added to site Successfully', 'status_code': 200})
            return Response({'status_text': 'Invalid key or site_id', 'status_code': 400}, status=400)

        elif service == 'AssignContentToMirror':
            login_key = request.data.get('login_key')
            mirror_id = request.data.get('mirror_id')
            content_ids = request.data.get('content_ids', [])
            administrator = get_administrator(login_key)
            if administrator and mirror_id:
                owner_id = administrator.get('_id')
                owned_query = {'administrator_id': {'$in': [owner_id, str(owner_id)]}}
                mirror = None
                try:
                    mirror = mirror_collection.find_one({'_id': ObjectId(mirror_id), **owned_query})
                except Exception:
                    mirror = mirror_collection.find_one({'_id': mirror_id, **owned_query})
                
                if mirror:
                    existing_contents = mirror.get('contents', [])
                    existing_str_ids = {str(c) for c in existing_contents}
                    updated_contents = list(existing_contents)
                    
                    for cid in content_ids:
                        try:
                            content_object_id = ObjectId(cid)
                        except Exception:
                            content_object_id = cid
                        owned_content = content_collection.find_one({'_id': content_object_id, **owned_query})
                        if not owned_content:
                            continue
                        if str(cid) not in existing_str_ids:
                            updated_contents.append(content_object_id)
                            existing_str_ids.add(str(cid))
                    
                    mirror_collection.update_one(
                        {'_id': mirror.get('_id')},
                        {'$set': {'contents': updated_contents}}
                    )
                    return Response({'status_text': 'Contents added to mirror successfully', 'status_code': 200})
                return Response({'status_text': 'Mirror not found', 'status_code': 404}, status=404)
            return Response({'status_text': 'Invalid key or mirror_id', 'status_code': 400}, status=400)

        elif service == 'RemoveContentFromMirror':
            login_key = request.data.get('login_key')
            mirror_id = request.data.get('mirror_id')
            content_id = request.data.get('content_id')
            administrator = get_administrator(login_key)
            if administrator and mirror_id and content_id:
                mirror = None
                try:
                    mirror = mirror_collection.find_one({'_id': ObjectId(mirror_id)})
                except Exception:
                    mirror = mirror_collection.find_one({'_id': mirror_id})
                
                if mirror:
                    current_contents = mirror.get('contents', [])
                    updated_contents = [
                        cid for cid in current_contents
                        if str(cid) != str(content_id)
                    ]
                    current_disabled = mirror.get('disabled_contents', [])
                    updated_disabled = [
                        cid for cid in current_disabled
                        if str(cid) != str(content_id)
                    ]
                    content_durations = mirror.get('content_durations', {})
                    if str(content_id) in content_durations:
                        del content_durations[str(content_id)]

                    mirror_collection.update_one(
                        {'_id': mirror['_id']},
                        {'$set': {
                            'contents': updated_contents,
                            'disabled_contents': updated_disabled,
                            'content_durations': content_durations,
                            'last_refresh_timestamp': time.time()
                        }}
                    )

                    # Delete content document since contents belong directly to this mirror
                    try:
                        c_id_obj = ObjectId(content_id)
                    except Exception:
                        c_id_obj = content_id
                    content_collection.delete_one({'_id': c_id_obj})

                    # Broadcast reload event to active viewers and Virtual Screen
                    try:
                        from channels.layers import get_channel_layer
                        from asgiref.sync import async_to_sync
                        channel_layer = get_channel_layer()
                        if channel_layer:
                            msg = {
                                'type': 'update.mirror',
                                'data': {
                                    'service': 'ReloadContents',
                                    'mirror_id': str(mirror['_id']),
                                    'timestamp': time.time()
                                }
                            }
                            async_to_sync(channel_layer.group_send)("mirror_group", msg)
                            async_to_sync(channel_layer.group_send)(f"mirror_{str(mirror['_id'])}", msg)
                    except Exception as e:
                        print(f"Error broadcasting ReloadContents on RemoveContent: {e}")

                    return Response({'status_text': 'Content removed from mirror successfully', 'status_code': 200})
                return Response({'status_text': 'Mirror not found', 'status_code': 404}, status=404)
            return Response({'status_text': 'Invalid key, mirror_id, or content_id', 'status_code': 400}, status=400)

        elif service in ['UpdateMirrorPlaylistOrder', 'ReorderMirrorContents']:
            login_key = request.data.get('login_key')
            mirror_id = request.data.get('mirror_id')
            ordered_content_ids = request.data.get('ordered_content_ids', [])
            administrator = get_administrator(login_key)
            if administrator and mirror_id:
                mirror = None
                try:
                    mirror = mirror_collection.find_one({'_id': ObjectId(mirror_id)})
                except Exception:
                    mirror = mirror_collection.find_one({'_id': mirror_id})
                
                if mirror:
                    new_order = []
                    seen = set()
                    for cid in ordered_content_ids:
                        cid_str = str(cid)
                        if cid_str not in seen:
                            try:
                                new_order.append(ObjectId(cid_str))
                            except Exception:
                                new_order.append(cid_str)
                            seen.add(cid_str)
                    
                    for cid in mirror.get('contents', []):
                        if str(cid) not in seen:
                            new_order.append(cid)
                            seen.add(str(cid))
                    
                    mirror_collection.update_one({'_id': mirror.get('_id')}, {'$set': {'contents': new_order}})
                    return Response({'status_text': 'Playlist order updated successfully', 'status_code': 200})
                return Response({'status_text': 'Mirror not found', 'status_code': 404}, status=404)
            return Response({'status_text': 'Invalid key or mirror_id', 'status_code': 400}, status=400)
        
        elif service == 'GetMySites':
            login_key = request.data.get('login_key')
            administrator = get_administrator(login_key)
            if administrator:
                administrator_id = administrator.get('_id')
                MySites = sites_collection.find({
                    'administrator_id': {'$in': [administrator_id, str(administrator_id), ObjectId(administrator_id)]}
                })
                MySites_list = []
                for site in MySites:
                    s = dict(site)
                    if '_id' in s:
                        s['_id'] = str(s['_id'])
                    if 'administrator_id' in s:
                        s['administrator_id'] = str(s['administrator_id'])
                    if 'site_id' in s:
                        s['site_id'] = str(s['site_id'])
                    MySites_list.append(s)
                return Response(status=200, data={'status_text': 'ok', 'data': MySites_list})
            return Response(status=200, data={'status_text': 'ok', 'data': []})
                      
        elif service == 'GetMyContents':
            login_key = request.data.get('login_key')
            administrator = get_administrator(login_key)
            if administrator:
                administrator_id = administrator.get('_id')
                MyContents = content_collection.find({
                    'administrator_id': {'$in': [administrator_id, str(administrator_id), ObjectId(administrator_id)]}
                })
                MyContent_list = []
                for content in MyContents:
                    c = dict(content)
                    if '_id' in c:
                        c['_id'] = str(c['_id'])
                    if 'administrator_id' in c:
                        c['administrator_id'] = str(c['administrator_id'])
                    if 'site_id' in c and c['site_id']:
                        c['site_id'] = str(c['site_id'])
                    if 'mirror_id' in c and c['mirror_id']:
                        c['mirror_id'] = str(c['mirror_id'])
                    MyContent_list.append(c)
                return Response({'status_text': 'ok', 'status_code': 200, 'data': MyContent_list})
            return Response({'status_text': 'ok', 'status_code': 200, 'data': []})
        
        elif service == 'GetContentsForThisMirror':
            login_key = request.data.get('login_key')
            mirror_id = request.data.get('mirror_id')
            mirror = None
            if mirror_id:
                try:
                    mirror = mirror_collection.find_one({'_id': ObjectId(mirror_id)})
                except Exception:
                    mirror = mirror_collection.find_one({'_id': mirror_id})
            contents = []
            photo_duration = 5
            mirror_width = 1920
            mirror_height = 1080
            mirror_name = 'Mirror Display'
            if mirror:
                photo_duration = mirror.get('photo_duration', 5)
                mirror_width = mirror.get('mirror_width', 1920)
                mirror_height = mirror.get('mirror_height', 1080)
                mirror_name = mirror.get('mirror_name', 'Mirror Display')
                content_durations = mirror.get('content_durations', {})
                if 'contents' in mirror and isinstance(mirror['contents'], list):
                    disabled_set = {str(d) for d in mirror.get('disabled_contents', [])}
                    for cid in mirror['contents']:
                        try:
                            content_doc = content_collection.find_one({"_id": ObjectId(cid)})
                            if content_doc:
                                c = dict(content_doc)
                                if '_id' in c:
                                    c['_id'] = str(c['_id'])
                                if 'administrator_id' in c:
                                    c['administrator_id'] = str(c['administrator_id'])
                                if 'mirror_id' in c and c['mirror_id']:
                                    c['mirror_id'] = str(c['mirror_id'])
                                c['is_active'] = str(cid) not in disabled_set
                                c['duration'] = int(content_durations.get(str(cid), photo_duration))
                                contents.append(c)
                        except Exception:
                            pass
            return Response({
                'status_text': 'ok',
                'status_code': 200,
                'photo_duration': photo_duration,
                'mirror_width': mirror_width,
                'mirror_height': mirror_height,
                'mirror_name': mirror_name,
                'data': contents
            })

        elif service in ['UpdatePhotoSpeed', 'SetPhotoSpeed', 'UpdateMirrorSpeed', 'SetMirrorSpeed']:
            login_key = request.data.get('login_key')
            mirror_id = request.data.get('mirror_id')
            photo_duration = request.data.get('photo_duration')
            content_id = request.data.get('content_id')
            duration = request.data.get('duration')
            
            administrator = get_administrator(login_key)
            if not mirror_id or not administrator:
                return Response({'status_text': 'Unauthorized or missing mirror_id', 'status_code': 400}, status=400)
            
            mirror = None
            try:
                mirror = mirror_collection.find_one({'_id': ObjectId(mirror_id)})
            except Exception:
                mirror = mirror_collection.find_one({'_id': mirror_id})
            
            if not mirror:
                return Response({'status_text': 'Mirror not found', 'status_code': 404}, status=404)
            
            current_time = time.time()
            update_fields = {'last_refresh_timestamp': current_time}
            
            if photo_duration is not None:
                try:
                    update_fields['photo_duration'] = max(1, int(photo_duration))
                except Exception:
                    pass
            
            if content_id and duration is not None:
                try:
                    update_fields[f'content_durations.{str(content_id)}'] = max(1, int(duration))
                except Exception:
                    pass

            mirror_collection.update_one({'_id': mirror['_id']}, {'$set': update_fields})
            
            # Broadcast WebSocket event to connected mirrors
            try:
                from channels.layers import get_channel_layer
                from asgiref.sync import async_to_sync
                channel_layer = get_channel_layer()
                if channel_layer:
                    msg = {
                        'type': 'update.mirror',
                        'data': {
                            'service': 'UpdateSettings',
                            'mirror_id': str(mirror['_id']),
                            'photo_duration': update_fields.get('photo_duration', mirror.get('photo_duration', 5)),
                            'content_id': str(content_id) if content_id else None,
                            'duration': int(duration) if duration is not None else None,
                            'timestamp': current_time
                        }
                    }
                    async_to_sync(channel_layer.group_send)("mirror_group", msg)
            except Exception as e:
                print("Error broadcasting settings update:", e)

            return Response({
                'status_text': 'Photo speed updated successfully',
                'status_code': 200,
                'photo_duration': update_fields.get('photo_duration', mirror.get('photo_duration', 5)),
                'content_id': str(content_id) if content_id else None,
                'duration': int(duration) if duration is not None else None
            }, status=200)

        elif service in ['ToggleContentStatusOnMirror', 'SetContentMirrorStatus']:
            login_key = request.data.get('login_key')
            mirror_id = request.data.get('mirror_id')
            content_id = request.data.get('content_id')
            is_active_param = request.data.get('is_active')
            administrator = get_administrator(login_key)
            if administrator and mirror_id and content_id:
                mirror = None
                try:
                    mirror = mirror_collection.find_one({'_id': ObjectId(mirror_id)})
                except Exception:
                    mirror = mirror_collection.find_one({'_id': mirror_id})
                if not mirror:
                    return Response({'status_text': 'Mirror not found', 'status_code': 404}, status=404)
                
                disabled_list = list(mirror.get('disabled_contents', []))
                disabled_str_ids = {str(d) for d in disabled_list}
                cid_str = str(content_id)

                if is_active_param is None:
                    new_active = cid_str in disabled_str_ids
                else:
                    new_active = bool(is_active_param)

                if new_active:
                    disabled_list = [d for d in disabled_list if str(d) != cid_str]
                else:
                    if cid_str not in disabled_str_ids:
                        try:
                            disabled_list.append(ObjectId(cid_str))
                        except Exception:
                            disabled_list.append(cid_str)

                current_time = time.time()
                mirror_collection.update_one(
                    {'_id': mirror.get('_id')},
                    {'$set': {'disabled_contents': disabled_list, 'last_refresh_timestamp': current_time}}
                )

                disabled_set = {str(d) for d in disabled_list}
                active_contents = []
                for cid in mirror.get('contents', []):
                    if str(cid) in disabled_set:
                        continue
                    try:
                        c_doc = content_collection.find_one({'_id': ObjectId(cid)})
                        if c_doc:
                            c = dict(c_doc)
                            if '_id' in c: c['_id'] = str(c['_id'])
                            if 'mirror_id' in c and c['mirror_id']: c['mirror_id'] = str(c['mirror_id'])
                            if 'site_id' in c and c['site_id']: c['site_id'] = str(c['site_id'])
                            if 'administrator_id' in c: c['administrator_id'] = str(c['administrator_id'])
                            active_contents.append(c)
                    except Exception:
                        pass

                try:
                    from channels.layers import get_channel_layer
                    from asgiref.sync import async_to_sync
                    channel_layer = get_channel_layer()
                    if channel_layer:
                        msg = {
                            'type': 'update.mirror',
                            'data': {
                                'service': 'ReloadContents',
                                'mirror_id': str(mirror.get('_id')),
                                'timestamp': current_time,
                                'data': active_contents
                            }
                        }
                        async_to_sync(channel_layer.group_send)(f"mirror_{str(mirror.get('_id'))}", msg)
                        async_to_sync(channel_layer.group_send)("mirror_group", msg)
                except Exception as e:
                    print(f"Broadcast notice: {e}")

                return Response({
                    'status_text': 'Content status updated on mirror',
                    'status_code': 200,
                    'is_active': new_active,
                    'mirror_id': str(mirror.get('_id')),
                    'content_id': cid_str,
                    'active_count': len(active_contents)
                }, status=200)
            return Response({'status_text': 'Required mirror_id and content_id', 'status_code': 400}, status=400)    

        elif service == 'GetContentDetails':
            content_id = request.data.get('content_id')
            content = content_collection.find_one({'_id': ObjectId(content_id)}) if content_id else None
            if content:
                return Response({'status_text': 'ok', 'status_code': 200, 'data': serialize_doc(dict(content))})
            return Response({'status_text': 'Not found', 'status_code': 404}, status=404)
        
        elif service == 'GetMirrorDetails':
            mirror_id = request.data.get('mirror_id')
            mirror = mirror_collection.find_one({'_id': ObjectId(mirror_id)}) if mirror_id else None
            if mirror:
                return Response({'status_text': 'ok', 'status_code': 200, 'data': serialize_doc(dict(mirror))})
            return Response({'status_text': 'Not found', 'status_code': 404}, status=404)
            return Response({'status_text': 'Not found', 'status_code': 404}, status=404)
        
        elif service == 'AddSite':
            site_name = request.data.get('site_name')
            site_description = request.data.get('site_description', '')
            if not site_name:
                return Response({'status_code': 400, 'status_text': 'site_name is required'}, status=400)
            
            login_key = request.data.get('login_key')
            administrator = get_administrator(login_key)
            if administrator:
                administrator_id = administrator.get('_id')
                if count_administrator_sites(administrator_id) >= PLAN['sites']:
                    return Response({'status_code': 400, 'status_text': 'This plan allows up to 10 sites.'}, status=400)
                sites_collection.insert_one({
                    "site_name": site_name,
                    "site_description": site_description or '',
                    "administrator_id": ObjectId(administrator_id)
                })
                return Response({'status_text': 'ok', 'status_code': 200})
            return Response({'status_text': 'check key', 'status_code': 401}, status=401)
                
        elif service == 'AddMirror':
            login_key = request.data.get('login_key')
            administrator = get_administrator(login_key)
            if not administrator:
                return Response({'status_code': 401, 'status_text': 'invalid key'}, status=401)
            
            administrator_id = administrator.get('_id')
            if count_administrator_mirrors(administrator_id) >= PLAN['sites'] * PLAN['mirrors_per_site']:
                return Response({'status_code': 400, 'status_text': 'This plan allows up to 50 mirrors at each of 10 sites.'}, status=400)
            mirror_name = request.data.get('mirror_name', '').strip()
            if not mirror_name:
                return Response({'status_code': 400, 'status_text': 'Mirror Name is required.'}, status=400)

            # Mirror names must be unique across the system for direct streaming
            existing_mirror = mirror_collection.find_one({
                'mirror_name': {'$regex': f'^{re.escape(mirror_name)}$', '$options': 'i'}
            })
            if existing_mirror:
                return Response({
                    'status_code': 400,
                    'status_text': f'A mirror with the name "{mirror_name}" already exists. Mirror names must be unique for streaming.'
                }, status=400)

            mirror_description = request.data.get('mirror_description', '').strip()
            mirror_height = request.data.get('height', '1080')
            mirror_width = request.data.get('width', '1920')
            site_id = request.data.get('site_id')

            site_obj_id = None
            if site_id:
                try:
                    site_obj_id = ObjectId(site_id)
                except Exception:
                    site_obj_id = site_id

                site = sites_collection.find_one({
                    '_id': site_obj_id,
                    'administrator_id': {'$in': administrator_ids(administrator_id)}
                })
                if not site:
                    return Response({'status_code': 400, 'status_text': 'Select a site belonging to your account.'}, status=400)
                if mirror_collection.count_documents({
                    'administrator_id': {'$in': administrator_ids(administrator_id)},
                    'site_id': {'$in': [site_obj_id, str(site_obj_id)]}
                }) >= PLAN['mirrors_per_site']:
                    return Response({'status_code': 400, 'status_text': 'This plan allows up to 50 mirrors per site.'}, status=400)

            current_time = time.time()
            res = mirror_collection.insert_one({
                "mirror_name": mirror_name,
                "mirror_description": mirror_description,
                "username": mirror_name,
                "password": "",
                "administrator_id": ObjectId(administrator_id),
                "mirror_height": mirror_height,
                "mirror_width": mirror_width,
                "site_id": site_obj_id,
                "playback_state": "playing",
                "is_active": True,
                "last_active_timestamp": current_time,
                "contents": []
            })
            return Response({'status_code': 200, 'status_text': 'ok', 'mirror_id': str(res.inserted_id)})

        elif service == 'DeleteMirror':
            login_key = request.data.get('login_key')
            mirror_id = request.data.get('mirror_id')
            administrator = get_administrator(login_key)
            if not administrator or not mirror_id:
                return Response({'status_code': 401, 'status_text': 'invalid key'}, status=401)
            try:
                mirror_object_id = ObjectId(mirror_id)
            except Exception:
                return Response({'status_code': 400, 'status_text': 'Invalid mirror_id'}, status=400)
            mirror = mirror_collection.find_one({'_id': mirror_object_id})
            if not mirror or str(mirror.get('administrator_id')) != str(administrator['_id']):
                return Response({'status_code': 404, 'status_text': 'Mirror not found'}, status=404)
            try:
                from app.conductor import get_or_create_conductor
                get_or_create_conductor(mirror_object_id).publish('eligibility_status', {'status_code': 600, 'status_text': 'Mirror deleted'}, retain=True)
            except Exception as exc:
                print(f"Could not publish mirror deletion status: {exc}", flush=True)
            mirror_collection.delete_one({'_id': mirror_object_id})
            return Response({'status_code': 200, 'status_text': 'Mirror deleted'})

        elif service == 'SetMirrorActiveStatus':
            administrator = get_administrator(request.data.get('login_key'))
            mirror_id = request.data.get('mirror_id')
            if not administrator or not mirror_id:
                return Response({'status_code': 401, 'status_text': 'Sign in and select a mirror.'}, status=401)
            try:
                mirror_object_id = ObjectId(mirror_id)
            except Exception:
                return Response({'status_code': 400, 'status_text': 'Invalid mirror_id'}, status=400)
            mirror = mirror_collection.find_one({'_id': mirror_object_id})
            if not mirror or str(mirror.get('administrator_id')) != str(administrator['_id']):
                return Response({'status_code': 404, 'status_text': 'Mirror not found'}, status=404)
            is_active = request.data.get('is_active') is True
            mirror_collection.update_one(
                {'_id': mirror_object_id},
                {'$set': {
                    'is_active': is_active,
                    'status': 'active' if is_active else 'inactive',
                    'last_refresh_timestamp': time.time()
                }}
            )
            try:
                from app.conductor import get_or_create_conductor
                conductor = get_or_create_conductor(mirror_object_id)
                conductor.notify_state_change()
                if is_active:
                    conductor.start()
                    fresh_mirror = mirror_collection.find_one({'_id': mirror_object_id})
                    if fresh_mirror:
                        conductor.publish('init', conductor.get_initial_state(fresh_mirror), retain=True)
                else:
                    conductor.publish('eligibility_status', {'status_code': 600, 'status_text': 'Mirror is inactive'}, retain=True)
            except Exception as exc:
                print(f"Mirror status playback update notice: {exc}", flush=True)
            return Response({'status_code': 200, 'status_text': 'Mirror status updated.', 'is_active': is_active})

        elif service == 'DeleteSite':
            login_key = request.data.get('login_key')
            site_id = request.data.get('site_id')
            administrator = get_administrator(login_key)
            if administrator and site_id:
                sites_collection.delete_one({'_id': ObjectId(site_id)})
                return Response({'status_code': 200, 'status_text': 'ok'})
            return Response({'status_code': 401, 'status_text': 'invalid key'}, status=401)

        elif service == 'DeleteContent':
            login_key = request.data.get('login_key')
            content_id = request.data.get('content_id')
            administrator = get_administrator(login_key)
            if administrator and content_id:
                try:
                    content_object_id = ObjectId(content_id)
                except Exception:
                    return Response({'status_code': 400, 'status_text': 'Invalid content id'}, status=400)
                owner_id = administrator.get('_id')
                owner_query = {'administrator_id': {'$in': [owner_id, str(owner_id)]}}
                content = content_collection.find_one({'_id': content_object_id, **owner_query})
                if not content:
                    return Response({'status_code': 404, 'status_text': 'Content not found'}, status=404)
                if content and 'content_url' in content:
                    try:
                        delete_file_from_s3(content["content_url"])
                    except Exception:
                        pass
                    try:
                        local_name = content["content_url"].split('/')[-1]
                        local_path = os.path.join('Contents', local_name)
                        if os.path.exists(local_path):
                            os.remove(local_path)
                    except Exception:
                        pass
                mirror_collection.update_many(owner_query, {'$pull': {'contents': content_object_id}})
                content_collection.delete_one({'_id': content_object_id, **owner_query})
                return Response({'status_code': 200, 'status_text': 'ok'})
            return Response({'status_code': 401, 'status_text': 'invalid key'}, status=401)

        elif service == 'RegisterSite':
            site_name = request.data.get('site_name')
            site_image_url = request.data.get('site_image_url', '')
            administrator_username = request.data.get('administrator_username')
            site_description = request.data.get('site_description', '')
            if not site_name or not administrator_username:
                return Response({"status_text": "Required site_name and administrator_username"}, status=400)
            
            administrator = administrators_collection.find_one({"username": administrator_username})
            if administrator is not None:
                if count_administrator_sites(administrator.get('_id')) >= PLAN['sites']:
                    return Response({'status_code': 400, 'status_text': 'This plan allows up to 10 sites.'}, status=400)
                site_data = {
                    "site_name": site_name,
                    "administrator_id": administrator.get('_id'),
                    "site_description": site_description,
                    "site_image_url": site_image_url
                }
                sites_collection.insert_one(site_data)
                return Response({'status_text': 'Registered Successfully', 'status_code': 200})
            return Response({'status_text': 'UnAuthorised', 'status_code': 401}, status=401)

        elif service in ['RefreshMirror', 'ReloadMirror', 'ReloadMirrorContents']:
            mirror_id = request.data.get('mirror_id')
            if not mirror_id:
                return Response({'status_text': 'Required mirror_id', 'status_code': 400}, status=400)
            
            mirror = None
            try:
                mirror = mirror_collection.find_one({'_id': ObjectId(mirror_id)})
            except Exception:
                mirror = mirror_collection.find_one({'_id': mirror_id})
            
            if not mirror:
                return Response({'status_text': 'Mirror not found', 'status_code': 404}, status=404)
            
            current_time = time.time()
            mirror_collection.update_one({'_id': mirror.get('_id')}, {'$set': {'last_refresh_timestamp': current_time}})
            
            disabled_set = {str(d) for d in mirror.get('disabled_contents', [])}
            content_list = []
            for cid in mirror.get('contents', []):
                if str(cid) in disabled_set:
                    continue
                try:
                    c_doc = content_collection.find_one({'_id': ObjectId(cid)})
                    if c_doc:
                        c = dict(c_doc)
                        if '_id' in c: c['_id'] = str(c['_id'])
                        if 'mirror_id' in c and c['mirror_id']: c['mirror_id'] = str(c['mirror_id'])
                        if 'site_id' in c and c['site_id']: c['site_id'] = str(c['site_id'])
                        if 'administrator_id' in c: c['administrator_id'] = str(c['administrator_id'])
                        content_list.append(c)
                except Exception:
                    pass

            try:
                from channels.layers import get_channel_layer
                from asgiref.sync import async_to_sync
                channel_layer = get_channel_layer()
                if channel_layer:
                    msg = {
                        'type': 'update.mirror',
                        'data': {
                            'service': 'ReloadContents',
                            'mirror_id': str(mirror.get('_id')),
                            'timestamp': current_time,
                            'data': content_list
                        }
                    }
                    async_to_sync(channel_layer.group_send)(f"mirror_{str(mirror.get('_id'))}", msg)
                    async_to_sync(channel_layer.group_send)("mirror_group", msg)
            except Exception as e:
                print(f"Channel broadcast notice: {e}")

            return Response({
                'status_text': 'Refresh signal sent to mirror successfully',
                'status_code': 200,
                'data': {
                    'mirror_id': str(mirror.get('_id')),
                    'contents_count': len(content_list),
                    'timestamp': current_time,
                    'contents': content_list
                }
            }, status=200)

        elif service in ['ControlPlayback', 'PlaybackControl']:
            mirror_id = request.data.get('mirror_id')
            action = request.data.get('action', 'toggle')
            if not mirror_id:
                return Response({'status_text': 'Required mirror_id', 'status_code': 400}, status=400)
            
            mirror = None
            try:
                mirror = mirror_collection.find_one({'_id': ObjectId(mirror_id)})
            except Exception:
                mirror = mirror_collection.find_one({'_id': mirror_id})
            
            if not mirror:
                return Response({'status_text': 'Mirror not found', 'status_code': 404}, status=404)
            
            current_state = mirror.get('playback_state', 'playing')
            if action == 'toggle':
                new_state = 'paused' if current_state == 'playing' else 'playing'
            elif action in ['pause', 'paused']:
                new_state = 'paused'
            else:
                new_state = 'playing'
            
            current_time = time.time()
            mirror_collection.update_one(
                {'_id': mirror.get('_id')},
                {'$set': {'playback_state': new_state, 'last_state_change_timestamp': current_time, 'last_active_timestamp': current_time}}
            )

            try:
                from channels.layers import get_channel_layer
                from asgiref.sync import async_to_sync
                channel_layer = get_channel_layer()
                if channel_layer:
                    msg = {
                        'type': 'update.mirror',
                        'data': {
                            'service': 'PlaybackControl',
                            'mirror_id': str(mirror.get('_id')),
                            'action': new_state,
                            'timestamp': current_time
                        }
                    }
                    async_to_sync(channel_layer.group_send)(f"mirror_{str(mirror.get('_id'))}", msg)
                    async_to_sync(channel_layer.group_send)("mirror_group", msg)
            except Exception as e:
                print(f"Playback control broadcast notice: {e}")

            # Instantly broadcast playback change to all SSE connected screens
            try:
                from app.conductor import get_or_create_conductor
                conductor = get_or_create_conductor(mirror['_id'])
                conductor.broadcast('playback_control', {
                    'action': new_state,
                    'mirror_id': str(mirror.get('_id')),
                    'timestamp': current_time
                })
                conductor.notify_state_change()
            except Exception as e:
                print(f"SSE playback control notice: {e}")

            return Response({
                'status_text': f'Playback state set to {new_state}',
                'status_code': 200,
                'playback_state': new_state,
                'mirror_id': str(mirror.get('_id'))
            }, status=200)

        elif service in ['GetMirrorLiveStatus', 'GetMirrorStatus']:
            mirror_id = request.data.get('mirror_id')
            if not mirror_id:
                return Response({'status_text': 'Required mirror_id', 'status_code': 400}, status=400)
            
            mirror = None
            try:
                mirror = mirror_collection.find_one({'_id': ObjectId(mirror_id)})
            except Exception:
                mirror = mirror_collection.find_one({'_id': mirror_id})
            
            if not mirror:
                return Response({'status_text': 'Mirror not found', 'status_code': 404}, status=404)
            
            current_time = time.time()
            # Virtual screen is a default mirror that is always logged in and continuously running
            mirror_collection.update_one({'_id': mirror.get('_id')}, {'$set': {'last_active_timestamp': current_time}})
            last_active = current_time
            is_online = True

            return Response({
                'status_text': 'ok',
                'status_code': 200,
                'data': {
                    'mirror_id': str(mirror.get('_id')),
                    'mirror_name': mirror.get('mirror_name', 'Mirror Display'),
                    'mirror_description': mirror.get('mirror_description', ''),
                    'mirror_width': mirror.get('mirror_width', 1920),
                    'mirror_height': mirror.get('mirror_height', 1080),
                    'username': mirror.get('username', ''),
                    'password': mirror.get('password', ''),
                    'active_content_id': mirror.get('active_content_id'),
                    'active_content_index': mirror.get('active_content_index', 0),
                    'playback_state': mirror.get('playback_state') or 'playing',
                    'is_active': mirror.get('is_active', str(mirror.get('status', '')).lower() not in ('inactive', 'disabled')),
                    'last_active_timestamp': last_active,
                    'last_refresh_timestamp': mirror.get('last_refresh_timestamp', 0),
                    'sync_timestamp': mirror.get('sync_timestamp', 0),
                    'slide_start_timestamp': mirror.get('slide_start_timestamp', 0),
                    'photo_duration': mirror.get('photo_duration', 5),
                    'is_online': is_online,
                    'server_time': current_time
                }
            }, status=200)

        elif service in ['SyncConnectedMirrors', 'SyncMirrors']:
            mirror_id = request.data.get('mirror_id')
            target_index = request.data.get('index')
            target_content_id = request.data.get('content_id')
            if not mirror_id:
                return Response({'status_text': 'Required mirror_id', 'status_code': 400}, status=400)
            
            mirror = None
            try:
                mirror = mirror_collection.find_one({'_id': ObjectId(mirror_id)})
            except Exception:
                mirror = mirror_collection.find_one({'_id': mirror_id})
            
            if not mirror:
                return Response({'status_text': 'Mirror not found', 'status_code': 404}, status=404)
            
            sync_index = int(target_index) if target_index is not None else mirror.get('active_content_index', 0)
            sync_cid = target_content_id or mirror.get('active_content_id')
            current_time = time.time()

            mirror_collection.update_one(
                {'_id': mirror.get('_id')},
                {'$set': {'sync_timestamp': current_time, 'active_content_index': sync_index, 'active_content_id': sync_cid, 'last_active_timestamp': current_time}}
            )

            site_id = mirror.get('site_id')
            if site_id:
                try:
                    mirror_collection.update_many(
                        {'site_id': site_id},
                        {'$set': {'sync_timestamp': current_time, 'active_content_index': sync_index, 'active_content_id': sync_cid}}
                    )
                except Exception:
                    pass

            try:
                from channels.layers import get_channel_layer
                from asgiref.sync import async_to_sync
                channel_layer = get_channel_layer()
                if channel_layer:
                    msg = {
                        'type': 'update.mirror',
                        'data': {
                            'service': 'SyncSlide',
                            'source_mirror_id': str(mirror.get('_id')),
                            'site_id': str(site_id) if site_id else None,
                            'index': sync_index,
                            'content_id': sync_cid,
                            'timestamp': current_time
                        }
                    }
                    async_to_sync(channel_layer.group_send)(f"mirror_{str(mirror.get('_id'))}", msg)
                    async_to_sync(channel_layer.group_send)("mirror_group", msg)
            except Exception as e:
                print(f"Sync broadcast notice: {e}")

            # Keep the web virtual screen and physical viewers on the same
            # MQTT trigger path for an operator-selected slide.
            try:
                from app.conductor import get_or_create_conductor
                conductor = get_or_create_conductor(mirror.get('_id'))
                item = None
                if sync_cid:
                    try:
                        item_doc = content_collection.find_one({'_id': ObjectId(str(sync_cid))})
                        item = serialize_doc(dict(item_doc)) if item_doc else None
                    except Exception:
                        item = None
                if item:
                    conductor.broadcast('slide_change', {
                        'index': sync_index,
                        'content_id': str(sync_cid),
                        'content_url': item.get('content_url'),
                        'content_format': item.get('content_format'),
                        'content': item,
                        'duration': int(mirror.get('content_durations', {}).get(str(sync_cid), mirror.get('photo_duration', 5))),
                        'timestamp': current_time
                    })
            except Exception as e:
                print(f"MQTT sync trigger notice: {e}")

            return Response({
                'status_text': 'Sync signal broadcast to connected mirrors',
                'status_code': 200,
                'data': {
                    'index': sync_index,
                    'content_id': sync_cid,
                    'timestamp': current_time
                }
            }, status=200)

        return Response({'status_text': 'Unknown service', 'status_code': 400}, status=400)


class Mirror(APIView):

    def post(self, request):
        if 'service' in request.data:
            service = request.data['service']
        else:
            return Response({'status_code': 400, 'status_text': 'Required service'}, status=400)

        if service == 'login':
            mirror_name = request.data.get('mirror_name')
            mirror_id = request.data.get('mirror_id')
            username = request.data.get('username')
            password = request.data.get('password')
            mirror = None

            if mirror_name:
                import re
                clean_name = str(mirror_name).strip()
                mirror = mirror_collection.find_one({'mirror_name': clean_name})
                if not mirror:
                    mirror = mirror_collection.find_one({'mirror_name': {'$regex': f'^{re.escape(clean_name)}$', '$options': 'i'}})

            if not mirror and mirror_id:
                try:
                    mirror = mirror_collection.find_one({'_id': ObjectId(mirror_id)})
                except Exception:
                    mirror = mirror_collection.find_one({'_id': mirror_id})

            if not mirror and username and password:
                mirror = mirror_collection.find_one({"username": username, "password": password})

            if not mirror and username:
                clean_u = str(username).strip()
                mirror = mirror_collection.find_one({'mirror_name': clean_u})
                if not mirror:
                    mirror = mirror_collection.find_one({'username': clean_u})

            if mirror:
                mirror_status = str(mirror.get('status', '')).strip().lower()
                if mirror.get('is_active') is False or mirror_status in ('inactive', 'disabled'):
                    return Response({
                        'status_text': 'This mirror is inactive. Reactivate it in the MirrorLink control panel to resume playback.',
                        'status_code': 423,
                        'mirror_active': False,
                        'mirror_id': str(mirror.get('_id')),
                    }, status=423)
                owner = administrators_collection.find_one({'_id': mirror.get('administrator_id')})
                if not plan_is_active(owner):
                    return Response({'status_text': 'This mirror is in setup mode. Subscribe to activate live playback.', 'status_code': 402, 'plan_active': False, 'mirror_id': str(mirror.get('_id'))}, status=402)
                current_time = time.time()
                mirror_collection.update_one(
                    {'_id': mirror.get('_id')},
                    {'$set': {'last_active_timestamp': current_time, 'status': 'active'}}
                )
                # Start the per-mirror server playback clock from the login
                # branch that actually handles viewer service='login'.
                try:
                    from app.conductor import get_or_create_conductor
                    get_or_create_conductor(mirror.get('_id')).start()
                except Exception as exc:
                    print(f"Virtual screen startup notice: {exc}", flush=True)
                photo_duration = mirror.get('photo_duration', 5)
                content_durations = mirror.get('content_durations', {})
                disabled_set = {str(d) for d in mirror.get('disabled_contents', [])}
                content_list = []
                for cid in mirror.get('contents', []):
                    if str(cid) in disabled_set:
                        continue
                    try:
                        c_doc = content_collection.find_one({'_id': ObjectId(cid)})
                        if c_doc:
                            c = dict(c_doc)
                            if '_id' in c:
                                c['_id'] = str(c['_id'])
                            if 'mirror_id' in c and c['mirror_id']:
                                c['mirror_id'] = str(c['mirror_id'])
                            if 'site_id' in c and c['site_id']:
                                c['site_id'] = str(c['site_id'])
                            if 'administrator_id' in c:
                                c['administrator_id'] = str(c['administrator_id'])
                            c['duration'] = int(content_durations.get(str(cid), photo_duration))
                            content_list.append(c)
                    except Exception:
                        pass
                return Response({
                    'status_text': 'ok',
                    'status_code': 200,
                    'mirror_id': str(mirror.get('_id')),
                    'mirror_name': mirror.get('mirror_name', ''),
                    'photo_duration': photo_duration,
                    'active_content_index': mirror.get('active_content_index', 0),
                    'sync_timestamp': mirror.get('last_sync_timestamp', 0),
                    'playback_state': mirror.get('playback_state', 'playing'),
                    'data': content_list
                })
            return Response({'status_text': 'Mirror display not found. Check the mirror name.', 'status_code': 404}, status=404)
            
        elif service == 'register':
            required_data = ['username', 'password', 'site_name', 'administrator_username', 'mirror_name']
            if not all(field in request.data for field in required_data):
                return Response({"status_text": "Required keys: username, password, site_name, administrator_username, mirror_name"}, status=400)
            username = request.data['username']
            password = request.data['password']
            site_name = request.data['site_name']
            administrator_username = request.data['administrator_username']
            mirror_name = request.data['mirror_name']
            mirror_description = request.data.get('mirror_description', '')
            if mirror_collection.find_one({'username': username}):
                return Response({'status': 'Username Already taken', 'status_code': 400}, status=400)
            administrator = administrators_collection.find_one({"username": administrator_username})
            if administrator:
                site = sites_collection.find_one({'site_name': site_name, 'administrator_id': {'$in': [administrator.get("_id"), str(administrator.get("_id"))]}})
                administrator_id = administrator.get('_id')
                if count_administrator_mirrors(administrator_id) >= PLAN['sites'] * PLAN['mirrors_per_site']:
                    return Response({'status_code': 400, 'status_text': 'This plan allows up to 50 mirrors at each of 10 sites.'}, status=400)
                if site and mirror_collection.count_documents({
                    'administrator_id': {'$in': administrator_ids(administrator_id)},
                    'site_id': {'$in': [site.get('_id'), str(site.get('_id'))]}
                }) >= PLAN['mirrors_per_site']:
                    return Response({'status_code': 400, 'status_text': 'This plan allows up to 50 mirrors per site.'}, status=400)
                mirror_data = {
                    "username": username,
                    "password": password,
                    "site_id": site.get("_id") if site else None,
                    "administrator_id": administrator.get('_id'),
                    "mirror_name": mirror_name,
                    "mirror_description": mirror_description,
                    "is_active": True,
                    "mirror_login_return_key": username + password,
                    "websocket_channel_name": None,
                    "contents": []
                }
                mirror_collection.insert_one(mirror_data)
                return Response({'status_text': 'Registered Successfully', 'status_code': 200})
            return Response({'status_text': 'UnAuthorised', 'status_code': 401}, status=401)

        elif service in ['CheckMirrorUpdate', 'GetMirrorUpdate', 'GetMirrorContents', 'login', 'ConnectMirror', 'StreamMirror']:
            mirror_name = request.data.get('mirror_name')
            username = request.data.get('username')
            password = request.data.get('password')
            mirror_id = request.data.get('mirror_id')
            mirror = None
            if mirror_name:
                import re
                clean_name = str(mirror_name).strip()
                mirror = mirror_collection.find_one({'mirror_name': clean_name})
                if not mirror:
                    mirror = mirror_collection.find_one({'mirror_name': {'$regex': f'^{re.escape(clean_name)}$', '$options': 'i'}})
            if not mirror and mirror_id:
                try:
                    mirror = mirror_collection.find_one({'_id': ObjectId(mirror_id)})
                except Exception:
                    mirror = mirror_collection.find_one({'_id': mirror_id})
            if not mirror and username:
                clean_u = str(username).strip()
                mirror = mirror_collection.find_one({'mirror_name': clean_u})
                if not mirror:
                    mirror = mirror_collection.find_one({'username': clean_u})
            if not mirror and username and password:
                mirror = mirror_collection.find_one({'username': username, 'password': password})
            if not mirror and (username or password):
                client_key = (username or '') + (password or '')
                mirror = mirror_collection.find_one({'mirror_login_return_key': client_key})
            
            if not mirror:
                return Response({'status_text': 'Mirror not found. Check mirror name.', 'status_code': 404}, status=404)

            owner = administrators_collection.find_one({'_id': mirror.get('administrator_id')})
            if mirror.get('is_active') is False or str(mirror.get('status', '')).lower() in ('inactive', 'disabled'):
                return Response({'status_text': 'This mirror is inactive and cannot play content.', 'status_code': 423, 'mirror_active': False}, status=423)
            if not plan_is_active(owner):
                return Response({'status_text': 'This mirror is in setup mode. Subscribe to activate live playback.', 'status_code': 402, 'plan_active': False, 'mirror_id': str(mirror.get('_id'))}, status=402)
            
            disabled_set = {str(d) for d in mirror.get('disabled_contents', [])}
            content_durations = mirror.get('content_durations', {})
            photo_duration = mirror.get('photo_duration', 5)
            content_list = []
            for cid in mirror.get('contents', []):
                if str(cid) in disabled_set:
                    continue
                try:
                    c_doc = content_collection.find_one({'_id': ObjectId(cid)})
                    if c_doc:
                        c = dict(c_doc)
                        if '_id' in c: c['_id'] = str(c['_id'])
                        if 'mirror_id' in c and c['mirror_id']: c['mirror_id'] = str(c['mirror_id'])
                        if 'site_id' in c and c['site_id']: c['site_id'] = str(c['site_id'])
                        if 'administrator_id' in c: c['administrator_id'] = str(c['administrator_id'])
                        c['duration'] = int(content_durations.get(str(cid), photo_duration))
                        content_list.append(c)
                except Exception:
                    pass

            current_time = time.time()
            mirror_collection.update_one({'_id': mirror.get('_id')}, {'$set': {'last_active_timestamp': current_time, 'status': 'active'}})
            # A logged-in display activates its server-side virtual screen clock.
            # The clock publishes each next-content trigger to the mirror MQTT topic.
            try:
                from app.conductor import get_or_create_conductor
                get_or_create_conductor(mirror.get('_id')).start()
            except Exception as exc:
                print(f"Virtual screen startup notice: {exc}")

            return Response({
                'status_text': 'ok',
                'status_code': 200,
                'last_refresh_timestamp': mirror.get('last_refresh_timestamp', 0),
                'mirror_id': str(mirror.get('_id')),
                'playback_state': mirror.get('playback_state', 'playing'),
                'photo_duration': photo_duration,
                'active_content_id': mirror.get('active_content_id'),
                'active_content_index': mirror.get('active_content_index', 0),
                'sync_timestamp': mirror.get('sync_timestamp', 0),
                'data': content_list
            }, status=200)

        elif service == 'ReportContentDuration':
            mirror_id = request.data.get('mirror_id')
            content_id = request.data.get('content_id')
            duration = request.data.get('duration')
            try:
                duration = max(1, int(float(duration)))
                mirror = mirror_collection.find_one({'_id': ObjectId(mirror_id)})
                if not mirror or not content_id:
                    raise ValueError('missing mirror or content')
                previous = int(mirror.get('content_durations', {}).get(str(content_id), 0) or 0)
                if abs(previous - duration) > 1:
                    mirror_collection.update_one(
                        {'_id': mirror['_id']},
                        {'$set': {f'content_durations.{content_id}': duration}}
                    )
                    from app.conductor import notify_mirror_updated
                    notify_mirror_updated(mirror['_id'])
                return Response({'status_text': 'Video duration recorded', 'status_code': 200})
            except Exception as exc:
                return Response({'status_text': f'Invalid video duration report: {exc}', 'status_code': 400}, status=400)

        elif service in ['UpdateActiveSlide', 'ReportActiveSlide', 'SlideChanged']:
            mirror_name = request.data.get('mirror_name')
            mirror_id = request.data.get('mirror_id')
            username = request.data.get('username')
            password = request.data.get('password')
            content_id = request.data.get('content_id')
            index = request.data.get('index', 0)

            mirror = None
            if mirror_name:
                import re
                clean_name = str(mirror_name).strip()
                mirror = mirror_collection.find_one({'mirror_name': clean_name})
                if not mirror:
                    mirror = mirror_collection.find_one({'mirror_name': {'$regex': f'^{re.escape(clean_name)}$', '$options': 'i'}})
            if not mirror and mirror_id:
                try:
                    mirror = mirror_collection.find_one({'_id': ObjectId(mirror_id)})
                except Exception:
                    mirror = mirror_collection.find_one({'_id': mirror_id})
            if not mirror and username:
                clean_u = str(username).strip()
                mirror = mirror_collection.find_one({'mirror_name': clean_u})
                if not mirror:
                    mirror = mirror_collection.find_one({'username': clean_u})
            if not mirror and username and password:
                mirror = mirror_collection.find_one({'username': username, 'password': password})
            if not mirror and (username or password):
                client_key = (username or '') + (password or '')
                mirror = mirror_collection.find_one({'mirror_login_return_key': client_key})

            if not mirror:
                return Response({'status_text': 'Mirror not found', 'status_code': 404}, status=404)

            current_time = time.time()
            cid_str = str(content_id) if content_id else None
            idx_val = int(index) if index is not None else 0

            mirror_collection.update_one(
                {'_id': mirror.get('_id')},
                {'$set': {
                    'active_content_id': cid_str,
                    'active_content_index': idx_val,
                    'last_active_timestamp': current_time,
                    'sync_timestamp': current_time,
                    'slide_start_timestamp': current_time
                }}
            )

            # Broadcast sync message to all other connected mirrors
            try:
                from channels.layers import get_channel_layer
                from asgiref.sync import async_to_sync
                channel_layer = get_channel_layer()
                if channel_layer:
                    msg = {
                        'type': 'update.mirror',
                        'data': {
                            'service': 'SyncSlide',
                            'source_mirror_id': str(mirror.get('_id')),
                            'site_id': str(mirror.get('site_id')) if mirror.get('site_id') else None,
                            'content_id': cid_str,
                            'index': idx_val,
                            'timestamp': current_time
                        }
                    }
                    async_to_sync(channel_layer.group_send)(f"mirror_{str(mirror.get('_id'))}", msg)
                    async_to_sync(channel_layer.group_send)("mirror_group", msg)
            except Exception:
                pass

            return Response({
                'status_text': 'Slide reported successfully',
                'status_code': 200,
                'mirror_id': str(mirror.get('_id')),
                'active_content_id': cid_str,
                'active_content_index': idx_val,
                'sync_timestamp': current_time
            }, status=200)

        return Response({'status_text': 'Unknown service', 'status_code': 400}, status=400)

    def get(self, request):
        return Response({'status_text': 'ok'}, status=200)


from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator


@method_decorator(csrf_exempt, name='dispatch')
class MirrorStreamView(View):
    """
    Real-Time Server-Sent Events (SSE) Stream for Display Viewers.
    Bypasses DRF Content Negotiation to natively support 'Accept: text/event-stream'.
    The server holds a persistent HTTP connection to every display, pushing
    synchronized slide transitions and playback commands to 100+ screens simultaneously.
    """

    def options(self, request, *args, **kwargs):
        from django.http import HttpResponse
        response = HttpResponse()
        response['Access-Control-Allow-Origin'] = '*'
        response['Access-Control-Allow-Headers'] = '*'
        response['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        return response

    def get(self, request, *args, **kwargs):
        from django.http import StreamingHttpResponse, HttpResponse
        from app.conductor import get_or_create_conductor
        import queue

        mirror_name = request.GET.get('mirror_name')
        mirror_id = request.GET.get('mirror_id')
        username = request.GET.get('username')

        mirror = None
        if mirror_name:
            import re
            clean_name = str(mirror_name).strip()
            mirror = mirror_collection.find_one({'mirror_name': clean_name})
            if not mirror:
                mirror = mirror_collection.find_one({'mirror_name': {'$regex': f'^{re.escape(clean_name)}$', '$options': 'i'}})

        if not mirror and mirror_id:
            try:
                mirror = mirror_collection.find_one({'_id': ObjectId(mirror_id)})
            except Exception:
                mirror = mirror_collection.find_one({'_id': mirror_id})

        if not mirror and username:
            clean_u = str(username).strip()
            mirror = mirror_collection.find_one({'mirror_name': clean_u})
            if not mirror:
                mirror = mirror_collection.find_one({'username': clean_u})

        if not mirror:
            resp = HttpResponse(
                "event: error\ndata: " + json.dumps({'status_code': 404, 'status_text': 'Mirror not found'}) + "\n\n",
                content_type="text/event-stream",
                status=404
            )
            resp['Access-Control-Allow-Origin'] = '*'
            return resp

        owner = administrators_collection.find_one({'_id': mirror.get('administrator_id')})
        if mirror.get('is_active') is False or str(mirror.get('status', '')).lower() in ('inactive', 'disabled'):
            resp = HttpResponse(
                "event: error\ndata: " + json.dumps({'status_code': 423, 'status_text': 'This mirror is inactive.'}) + "\n\n",
                content_type="text/event-stream", status=423
            )
            resp['Access-Control-Allow-Origin'] = '*'
            return resp
        if not plan_is_active(owner):
            resp = HttpResponse(
                "event: error\ndata: " + json.dumps({'status_code': 402, 'status_text': 'Subscribe to activate live playback.'}) + "\n\n",
                content_type="text/event-stream", status=402
            )
            resp['Access-Control-Allow-Origin'] = '*'
            return resp

        conductor = get_or_create_conductor(mirror['_id'])
        client_queue = queue.Queue(maxsize=100)
        conductor.add_listener(client_queue)

        async def event_stream():
            try:
                # 1. Send initial state immediately so the viewer displays media without waiting
                initial_state = await asyncio.to_thread(conductor.get_initial_state, mirror)
                yield f"event: init\ndata: {json.dumps(initial_state, default=str)}\n\n"

                # 2. Stream synchronized slide triggers pushed by the server conductor
                while True:
                    try:
                        msg = await asyncio.to_thread(client_queue.get, timeout=15)
                        yield f"event: {msg['event']}\ndata: {json.dumps(msg['data'], default=str)}\n\n"
                    except queue.Empty:
                        # Periodic keepalive comment to keep connection alive through Nginx & proxies
                        yield ": keepalive\n\n"
            except (GeneratorExit, asyncio.CancelledError):
                pass
            except Exception as e:
                import traceback
                print(f"[SSE Stream Error]: {traceback.format_exc()}")
            finally:
                conductor.remove_listener(client_queue)

        response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
        response['Cache-Control'] = 'no-cache, no-transform'
        response['X-Accel-Buffering'] = 'no'  # Disables Nginx response buffering for real-time delivery
        response['Access-Control-Allow-Origin'] = '*'
        response['Access-Control-Allow-Headers'] = '*'
        response['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        return response
