import base64
import os
import time
import json
import queue
import asyncio
from bson import ObjectId
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import administrators_collection, mirror_collection, sites_collection, content_collection
from s3_config import upload_file_to_s3, delete_file_from_s3


def get_administrator(login_key=None):
    if login_key:
        admin = administrators_collection.find_one({'login_key': login_key})
        if admin:
            return admin
    all_admins = list(administrators_collection.find())
    if all_admins:
        if login_key:
            administrators_collection.update_one({'_id': all_admins[0]['_id']}, {'$set': {'login_key': login_key}})
            all_admins[0]['login_key'] = login_key
        return all_admins[0]
    return None


def serialize_doc(obj):
    if isinstance(obj, ObjectId):
        return str(obj)
    if isinstance(obj, dict):
        return {k: serialize_doc(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [serialize_doc(v) for v in obj]
    return obj


class Administrator(APIView):
    
    def post(self, request):
        if 'service' in request.data:
            service = request.data['service']
            # print("Incoming service request:", service, "data:", request.data)
        else:
            return Response({'status_code': 400, 'status_text': 'Required service'}, status=400)

        if service == 'login':
            username = request.data.get('username')
            password = request.data.get('password')
            administrator = administrators_collection.find_one({"username": username, "password": password})
            if administrator:
                login_key = administrator.get('login_key') or (username + password)
                return Response({'status_text': 'ok', 'status_code': 200, 'login_key': login_key})
            return Response({'status_text': 'UnAuthorised', 'status_code': 401}, status=401)
        
        elif service == 'register':
            required_data = ['username', 'password', 'email']
            if not all(field in request.data for field in required_data):
                return Response({"status_text": "Required keys: username, password, email"}, status=400)
            username = request.data['username']
            password = request.data['password']
            email_id = request.data['email']
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
                "password": password,
                "email_id": email_id,
                "profile_image_url": profile_image_url,
                "contact": contact,
                "address_line_1": address_line_1,
                "address_line_2": address_line_2,
                "address_line_3": address_line_3,
                "login_key": username + password,
            }
            administrators_collection.insert_one(administrator_data)
            return Response({'status_text': 'Registered Successfully', 'status_code': 200, 'login_key': username + password})
            
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
                for mirror in mirrors:
                    mirror_id = mirror.get('_id') if isinstance(mirror, dict) else mirror
                    mirror_collection.update_one({'_id': ObjectId(mirror_id)}, {'$set': {'site_id': ObjectId(site_id)}})
                return Response({'status_text': 'Mirror added to site Successfully', 'status_code': 200})
            return Response({'status_text': 'Invalid key or site_id', 'status_code': 400}, status=400)

        elif service == 'AssignContentToMirror':
            login_key = request.data.get('login_key')
            mirror_id = request.data.get('mirror_id')
            content_ids = request.data.get('content_ids', [])
            administrator = get_administrator(login_key)
            if administrator and mirror_id:
                mirror = None
                try:
                    mirror = mirror_collection.find_one({'_id': ObjectId(mirror_id)})
                except Exception:
                    mirror = mirror_collection.find_one({'_id': mirror_id})
                
                if mirror:
                    existing_contents = mirror.get('contents', [])
                    existing_str_ids = {str(c) for c in existing_contents}
                    updated_contents = list(existing_contents)
                    
                    for cid in content_ids:
                        if str(cid) not in existing_str_ids:
                            try:
                                updated_contents.append(ObjectId(cid))
                            except Exception:
                                updated_contents.append(cid)
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
            mirror_name = request.data.get('mirror_name', '').strip()
            if not mirror_name:
                return Response({'status_code': 400, 'status_text': 'Mirror Name is required.'}, status=400)

            # Mirror names must be unique across the system for direct streaming
            import re
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
                "last_active_timestamp": current_time,
                "contents": []
            })
            return Response({'status_code': 200, 'status_text': 'ok', 'mirror_id': str(res.inserted_id)})

        elif service == 'DeleteMirror':
            login_key = request.data.get('login_key')
            mirror_id = request.data.get('mirror_id')
            administrator = get_administrator(login_key)
            if administrator and mirror_id:
                mirror_collection.delete_one({'_id': ObjectId(mirror_id)})
                return Response({'status_code': 200, 'status_text': 'ok'})
            return Response({'status_code': 401, 'status_text': 'invalid key'}, status=401)

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
                content = content_collection.find_one({'_id': ObjectId(content_id)})
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
                content_collection.delete_one({'_id': ObjectId(content_id)})
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
                current_time = time.time()
                mirror_collection.update_one(
                    {'_id': mirror.get('_id')},
                    {'$set': {'last_active_timestamp': current_time}}
                )
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
                mirror_data = {
                    "username": username,
                    "password": password,
                    "site_id": site.get("_id") if site else None,
                    "administrator_id": administrator.get('_id'),
                    "mirror_name": mirror_name,
                    "mirror_description": mirror_description,
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
