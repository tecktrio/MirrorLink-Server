import base64
import os
from bson import ObjectId
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import administrators_collection, mirror_collection, sites_collection, content_collection
from firebase_admin import storage
from firebase_config import configure, delete_file_from_public_url
configure()
bucket = storage.bucket()

class Administrator(APIView):
    
    def post(self, request):

        

        if 'service' in request.data:
            service = request.data['service']
        else:
            return Response({'status_code':200, 'status_text':'Required service'})

        if service == 'login':
            username = request.data['username']
            password = request.data['password']
            if administrators_collection.find_one({"username":username,"password":password}):
                    administrator = administrators_collection.find_one({"username":username,"password":password})
                    login_key = administrator.get('login_key')
                    return Response({'status_text':'ok','status_code':200,'login_key':login_key})
            return Response({'status_text':'UnAuthorised','status_code':401})
        
        elif service == 'register':
            required_data = ['username','password','email','profile_image_url','contact','address_line_1','address_line_2','address_line_3']
            print(request.data , )
            if  not all(field in request.data.keys() for field in required_data):
                return Response({"status_text":"requred keys 'username','password','email','profile_image_url','contact','address_line_1','address_line_2','address_line_3'"})
            username = request.data['username']
            password = request.data['password']
            email_id = request.data['email']
            profile_image_url = request.data['profile_image_url']
            contact = request.data['contact']
            address_line_1 = request.data['address_line_1']
            address_line_2 = request.data['address_line_2']
            address_line_3 = request.data['address_line_3']
            if administrators_collection.find_one({'email_id':email_id}):
                return Response({'status':'Email Already Exist Please Login'})
            if administrators_collection.find_one({'username':username}):
                return Response({'status':'Username Already taken'})
            administrator_data = {
                "username":username,
                "password":password,
                "email_id":email_id,
                "profile_image_url":profile_image_url,
                "contact":contact,
                "address_line_1":address_line_1,
                "address_line_2":address_line_2,
                "address_line_3":address_line_3,
                "login_key":username+password,
            }
            administrators_collection.insert_one(administrator_data)
            return Response({'status_text':'Registered Successfully','status_code':200})
            
        elif service == 'AddContent':       
                login_key = request.data['login_key']
                administrator = administrators_collection.find_one({'login_key':login_key})
                if administrator:
                    administrator_id = administrator.get('_id')
                    required_fields = ['content','content_title','content_description', 'mirror_id','site_id','file_extention']
                    content = request.data['content']
                    content_title = request.data['content_title']
                    content_description = request.data['content_description']
                    file_extention = request.data['file_extention']


                    file_data = content.split(',')[1]
                    decoded_file_data = base64.b64decode(file_data)
                    file_path = os.path.join('Contents', content_title)
                    file_path = file_path + '.'+file_extention
                    # print('full name',file_path)
                    with open(file_path, 'wb') as file:
                        file.write(decoded_file_data)
                    # Upload the file to Firebase Storage                        
                    try:
                        blob = bucket.blob(f'contents/{content_title}')
                        # print('Uploading file...')
                        
                        # Upload the file
                        blob.upload_from_filename(file_path)
                        # print('File uploaded successfully!')

                        # Make the file publicly accessible
                        blob.make_public()
                        # print(f'File is publicly accessible at: {blob.public_url}')
                    except Exception as e:
                        print(f'An error occurred: {e}')

                    # Get the public URL for the uploaded file
                    content_url = blob.public_url

                    # print('uploaded to firestorage')
                    mirror_id = request.data['mirror_id']
                    site_id = request.data['site_id']

                    order = 1

                    # Send the file URL back to the client
                    content_collection.insert_one({
                        "mirror_id":ObjectId(mirror_id) if mirror_id != "" else "",
                        "site_id":ObjectId(site_id) if site_id != "" else "",
                        "administrator_id":administrator_id,
                        "content_title":content_title,
                        "content_description":content_description,
                        "content_url":content_url,
                        "content_format":file_extention,
                        "is_active":False,
                        "order":order
                    })
                    os.remove(file_path)
                    return Response({'status_text':'content uploaded successfully','status_code':200})
                        
        elif service == 'GetMyMirrors':
            required_fields = [ 'login_key']
            login_key = request.data['login_key']
            administrator = administrators_collection.find_one({'login_key':login_key})
            if administrator:
                if not all(field in request.data for field in required_fields):
                    return Response({'status_text':'Required  login_key'})
                else:
                    site_id = request.data['site_id']
                    print(site_id)
                    administrator_id = administrator.get('_id')

                    if site_id:
                        MyMirrors = mirror_collection.find({'administrator_id':ObjectId(administrator_id), 'site_id':ObjectId(site_id)})
                    else:
                        MyMirrors = mirror_collection.find({'administrator_id':ObjectId(administrator_id)})
                    MyMirrors_list = []
                    for mirror in MyMirrors:
                        if '_id' in mirror:
                            mirror['_id'] = str(mirror['_id'])
                        if 'administrator_id' in mirror:
                            mirror['administrator_id'] = str(mirror['administrator_id'])
                        if 'site_id' in mirror:
                            mirror['site_id'] = str(mirror['site_id'])
                        MyMirrors_list.append(mirror)
                    return Response({'status_text':'Site created Successfully','status_code':200, 'data':MyMirrors_list})
            return Response({'status_text':'Invalid key','status_code':500})
        
        elif service == 'GetMySites':
            login_key = request.data['login_key']
            administrator = administrators_collection.find_one({'login_key':login_key})
            if administrator:
                administrator_id = administrator.get('_id')
                MySites = sites_collection.find({'administrator_id':administrator_id})
                MySites_list = []

                for site in MySites:
                    if '_id' in site:
                        site['_id'] = str(site['_id'])
                    if 'administrator_id' in site:
                        site['administrator_id'] = str(site['administrator_id'])
                    if 'site_id' in site:
                        site['site_id'] = str(site['site_id'])
                    MySites_list.append(site)
                print(MySites_list)
            
                return Response(status=200,data={'status_text':'ok','data':MySites_list})
            # return Response({'status_text':'Administrator not found','status_code':403},status=403)
                      
        elif service == 'GetMyContents':
            required_fields = ['login_key','site_id','mirror_id']
            login_key = request.data['login_key']
            administrator = administrators_collection.find_one({'login_key':login_key})
            administrator_id =administrator.get('_id')

            print(request.data)

            if not all(field in request.data for field in required_fields):
                MyContents = content_collection.find({'administrator_id':ObjectId(administrator_id)})
            else:
                site_id = request.data['site_id']
                mirror_id = request.data['mirror_id']
                MyContents = content_collection.find({'administrator_id':ObjectId(administrator_id), 'site_id':ObjectId(site_id), 'mirror_id':ObjectId(mirror_id)})
               
                       
            MyContent_list = []
            for content in MyContents:
                if '_id' in content:
                    content['_id'] = str(content['_id'])
                if 'administrator_id' in content:
                    content['administrator_id'] = str(content['administrator_id'])
                if 'site_id' in content:
                    content['site_id'] = str(content['site_id'])
                if 'mirror_id' in content:
                    content['mirror_id'] = str(content['mirror_id'])
                MyContent_list.append(content)
            return Response({'status_text':'ok','status_code':200,'data':MyContent_list})    

        elif service == 'GetContentDetails':
            required_fields = ['login_key','content_id']
            login_key = request.data['login_key']
            content_id = request.data['content_id']
            administrator = administrators_collection.find_one({'login_key':login_key})
            content = content_collection.find_one({'_id':ObjectId(content_id)})
               
                       
            # MyContent_list = []
            # for content in MyContents:
            if '_id' in content:
                content['_id'] = str(content['_id'])
            if 'administrator_id' in content:
                content['administrator_id'] = str(content['administrator_id'])
            if 'site_id' in content:
                content['site_id'] = str(content['site_id'])
            if 'mirror_id' in content:
                content['mirror_id'] = str(content['mirror_id'])
                # MyContent_list.append(content)
            return Response({'status_text':'ok','status_code':200,'data':content})
        
        elif service == 'GetMirrorDetails':
            required_fields = ['login_key','mirror_id']
            login_key = request.data['login_key']
            mirror_id = request.data['mirror_id']
            administrator = administrators_collection.find_one({'login_key':login_key})
            mirror = mirror_collection.find_one({'_id':ObjectId(mirror_id)})
               
                       
            # MyContent_list = []
            # for content in MyContents:
            if '_id' in mirror:
                mirror['_id'] = str(mirror['_id'])
            if 'administrator_id' in mirror:
                mirror['administrator_id'] = str(mirror['administrator_id'])
            if 'site_id' in mirror:
                mirror['site_id'] = str(mirror['site_id'])
            if 'mirror_id' in mirror:
                mirror['mirror_id'] = str(mirror['mirror_id'])
                # MyContent_list.append(content)
            return Response({'status_text':'ok','status_code':200,'data':mirror})
        
        elif service == 'AddSite':
            print(request.data)
            required_fields = ['site_description','site_name','login_key']
            # print(text_data_json)
            if not all(field in request.data for field in required_fields):
                return Response({'status_code':500, 'status_text':'Required site_description, site_name, login_key'},status=500)
            else:
                login_key = request.data['login_key']
                administrator = administrators_collection.find_one({"login_key":login_key})
                if administrator:
                    site_name = request.data['site_name']
                    site_description = request.data['site_description']
                    administrator_id = administrator.get('_id')
                    # MySites = sites_collection.find({'administrator_id':administrator_id})
                    sites_collection.insert_one({
                        "site_name":site_name,
                        "site_description":site_description,
                        "administrator_id":ObjectId(administrator_id)
                    })
                    return Response({'status_text':'ok', 'status_code':200})
                return Response({'status_text':'check key', 'status_code':401})
                
        elif service == 'AddMirror':
            login_key = request.data['login_key']
            required_fields = ['mirror_name','mirror_description','username','password', 'site_id','height','width','login_key']
            if not all(field in request.data for field in required_fields):
                return Response({'status_text':'Required mirror_name, mirror_description, username, password, site_id, height, width , login_key'})
            else:
                administrator = administrators_collection.find_one({'login_key':login_key})

                if administrator:
                    administrator_id = administrator.get('_id')

                    # MySites = sites_collection.find({'administrator_id':administrator_id})
                    mirror_name = request.data['mirror_name']
                    mirror_description = request.data['mirror_description']
                    username = request.data['username']
                    password = request.data['password']
                    site_id = request.data['site_id']
                    mirror_height = request.data['height']
                    mirror_width = request.data['width']

                    mirror_collection.insert_one({
                        "username":username,
                        "password":password,
                        "administrator_id":administrator_id,
                        "mirror_name":mirror_name,
                        "mirror_description":mirror_description,
                        "site_id":ObjectId(site_id) if site_id else "",
                        "mirror_height":mirror_height,
                        "mirror_width":mirror_width,
                    })

                    print('all set')

                    return Response({'status_code':200, 'status_text':'ok'})
                return Response({'status_code':401, 'status_text':'invalid key'})
        elif service == 'DeleteMirror':
            login_key = request.data['login_key']
            mirror_id = request.data['mirror_id']
            required_fields = ['login_key','mirror_id']
            if not all(field in request.data for field in required_fields):
                return Response({'status_text':'Required mirror_id,  login_key'})
            else:
                administrator = administrators_collection.find_one({'login_key':login_key})

                if administrator:
                    administrator_id = administrator.get('_id')

                    mirror_collection.delete_one({'_id':ObjectId(mirror_id)})
                    # print('all set')

                    return Response({'status_code':200, 'status_text':'ok'})
                return Response({'status_code':401, 'status_text':'invalid key'})
        elif service == 'DeleteSite':
            login_key = request.data['login_key']
            site_id = request.data['site_id']
            required_fields = ['login_key','site_id']
            if not all(field in request.data for field in required_fields):
                return Response({'status_text':'Required msite_id, login_key'})
            else:
                administrator = administrators_collection.find_one({'login_key':login_key})

                if administrator:
                    administrator_id = administrator.get('_id')

                    site = sites_collection.delete_one({'_id':ObjectId(site_id)})
                    if site.deleted_count > 0:
                        print("Document deleted successfully.")
                    else:
                        print("No document found with that ID.")

                    # print('all set')

                    return Response({'status_code':200, 'status_text':'ok'})
                return Response({'status_code':401, 'status_text':'invalid key'})
        elif service == 'DeleteContent':
            login_key = request.data['login_key']
            content_id = request.data['content_id']
            required_fields = ['login_key','content_id']
            if not all(field in request.data for field in required_fields):
                return Response({'status_text':'Required content_id, login_key'})
            else:
                administrator = administrators_collection.find_one({'login_key':login_key})

                if administrator:
                    administrator_id = administrator.get('_id')

                    content = content_collection.find_one({'_id':ObjectId(content_id)})
                    print('content url',content['content_url'])
                    delete_file_from_public_url(content["content_url"])
                    content_collection.delete_one({'_id':ObjectId(content_id)})
                    # print('all set')

                    return Response({'status_code':200, 'status_text':'ok'})
                return Response({'status_code':401, 'status_text':'invalid key'})

        elif service == 'RegisterSite':
            required_data = ['administrator_username','site_name','site_image_url','site_description']
            if  not all(field in request.data for field in required_data):
                return Response({"status_text":"requred keys 'administrator_username','site_name','site_image_url','site_description'"})
            site_name = request.data['site_name']
            site_image_url = request.data['site_image_url']
            administrator_username = request.data['administrator_username']
            site_description = request.data['site_description']
            if sites_collection.find_one({'site_name':site_name}):
                return Response({'status':'site name Already Exist'})
            administrator =  administrators_collection.find_one({"username":administrator_username})
            if administrator is not None:
                site_data = {
                    "site_name":site_name,
                    "administrator_id":administrator.get('_id'),
                    "site_name":site_name,
                    "site_description":site_description,
                    "site_image_url":site_image_url
                }
                sites_collection.insert_one(site_data)
                return Response({'status_text':'Registered Successfully','status_code':200})
            else:
                return Response({'status_text':'UnAuthorised','status_code':401})


class Mirror(APIView):

    def post(self, request):
        print('login service')
        if 'service' in request.data:
            service = request.data['service']
        else:
            return Response({'status_code':200, 'status_text':'Required service'})

        if service == 'login':
            print('login service')
            username = request.data['username']
            password = request.data['password']
            if mirror_collection.find_one({"username":username,"password":password}):   
                    mirror = mirror_collection.find_one({"username":username,"password":password})
                    contents = content_collection.find({'mirror_id':mirror.get('_id')})
                    content_list = []
                    for content in contents:
                        if '_id' in content:
                            content['_id'] = str(content['_id'])
                        if 'mirror_id' in content:
                            content['mirror_id'] = str(content['mirror_id'])
                        if 'site_id' in content:
                            content['site_id'] = str(content['site_id'])
                        if 'administrator_id' in content:
                            content['administrator_id'] = str(content['administrator_id'])
                        content_list.append(content)
                    return Response({'status_text':'ok','status_code':200,'data':content_list})
            
        elif service == 'register':
            required_data = ['username','password','site_name','administrator_username','mirror_name','mirror_description']
            if  not all(field in request.data for field in required_data):
                return Response({"status_text":"requred keys 'username','password','site_name','administrator_username','mirror_name','mirror_description'"})
            username = request.data['username']
            password = request.data['password']
            site_name = request.data['site_name']
            administrator_username = request.data['administrator_username']
            mirror_name = request.data['mirror_name']
            mirror_description = request.data['mirror_description']
            if mirror_collection.find_one({'username':username}):
                return Response({'status':'Username Already taken'})
            if administrators_collection.find_one({"username":administrator_username}):
                administrator =  administrators_collection.find_one({"username":administrator_username})
                if sites_collection.find_one({'site_name':site_name, 'administrator_id':administrator.get("_id")}):
                    site = sites_collection.find_one({'site_name':site_name, 'administrator_id':administrator.get("_id")})
                    mirror_data = {
                        "username":username,
                        "password":password,
                        "site_id":site.get("_id"),
                        "administrator_id":administrator.get('_id'),
                        "mirror_name":mirror_name,
                        "mirror_description":mirror_description,
                        "mirror_login_return_key":username+password,
                        "websocket_channel_name":None
                    }
                    mirror_collection.insert_one(mirror_data)
                    return Response({'status_text':'Registered Successfully','status_code':200})
                else:
                    return Response({'status_text':'Not Allowed','status_code':401}) 
            else:
                return Response({'status_text':'UnAuthorised','status_code':401})

    def get(self, request):

        Response({},status=500)