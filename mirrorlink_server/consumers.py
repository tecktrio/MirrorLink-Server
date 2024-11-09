import base64
import json
import os
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.layers import get_channel_layer
from app.models import mirror_collection, administrators_collection, content_collection, sites_collection
from firebase_admin import storage

from bson.objectid import ObjectId
users = []

class ControllerConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        # Accept the WebSocket connection
        query_string = self.scope['query_string'].decode('utf-8')
        params = dict(qc.split("=") for qc in query_string.split("&"))
        client_key = params.get('key') 

        if administrators_collection.find_one({'administrator_login_return_key':client_key}):
                # print('here')
            # if client_key ==  "key":
                print(self.channel_name)
                self.administrator = administrators_collection.find_one({'administrator_login_return_key':client_key})
                await self.accept()
                  # Add the connected client to a group (e.g., group name can be 'mirror_group')
                administrators_collection.update_one({'_id':self.administrator.get("_id")},{"$set":{'websocket_channel_name':self.channel_name}})
                await self.channel_layer.group_add("controller_group", self.channel_name)

                await self.channel_layer.group_send(
                        "controller_group",
                        {
                            'type': 'update.mirror',
                            'data': 'Welcome to MirrorLink Server, Now You can use the services like  - Upload_Content, Update_Mirror_Info, GetMyMirrors'
                        }
                    )

                # self.send(text_data="welcome to the service update mirror")

        else:
            self.close()

    async def disconnect(self, close_code):
        # print('here')
        # Remove the client's channel name from the list on disconnect
        if self.channel_name in users:
            users.remove(self.channel_name)

    async def receive(self, text_data):
        # Print the list of currently connected clients

        # formate
        """
        {
            "service":"upload_content",
            "mirror_name":"demo_mirror",
            "content":content,
            "content_title":"demo_title",
            "site_name":"demo_name"
        }
        """

        # Handle incoming message
        text_data_json = json.loads(text_data)
        service = text_data_json['service']

        # Get the channel layer
        channel_layer = get_channel_layer()
        # print(service)


        administrator_id = self.administrator.get("_id")
        # Broadcast the message to all connected clients
        # for channel in users:
        if service == 'AddContent':

            required_fields = ['mirror_id','content','content_title','content_description','site_id']
            # print(text_data_json)
            if not all(field in text_data_json for field in required_fields):
                await self.channel_layer.group_send(
                    "controller_group",
                    {
                        'type': 'update.mirror',
                        'data': 'Required fields - mirror_id, content , content_title, site_id,content_description'
                    }
                )
            else:
                mirror_id = text_data_json['mirror_id']
                content = text_data_json['content']
                content_title = text_data_json['content_title']
                content_description = text_data_json['content_description']
                site_id = text_data_json['site_id']

                # Remove the base64 header and decode the data
                file_data = content.split(',')[1]  # Remove "data:<type>;base64,"
                decoded_file_data = base64.b64decode(file_data)

                # Define the path where you want to save the uploaded file
                file_path = os.path.join('Contents', content_title)

                # Save the file to the server
                with open(file_path, 'wb') as file:
                    file.write(decoded_file_data)

                # Upload the file to Firebase Storage
                bucket = storage.bucket()
                blob = bucket.blob(f'uploads/{content_title}')
                blob.upload_from_filename(file_path)

                # Make the file publicly accessible
                blob.make_public()

                # Get the public URL for the uploaded file
                content_url = blob.public_url

                administrator_id = self.administrator.get('_id')
                # print('admin id',administrator_id)# Query the site
                site = sites_collection.find_one({'_id': ObjectId(site_id)})

                # Check if the site exists before proceeding
                if site:
                    # print(site)
                    
                    # Query the mirror using the site_id from the found site document
                    mirror = mirror_collection.find_one({
                        '_id': ObjectId(mirror_id),
                    })
                    
                    # Handle mirror not found
                    if mirror:
                        # print("Mirror found:", mirror)
                        order = 1

                        # Send the file URL back to the client
                        content_collection.insert_one({
                            "mirror_id":mirror.get('_id'),
                            "site_id":site.get("_id"),
                            "administrator_id":administrator_id,
                            "content_title":content_title,
                            "content_description":content_description,
                            "content_url":content_url,
                            "is_active":False,
                            "order":order
                        })

                        
                        await self.channel_layer.group_send(
                                "controller_group",
                                {
                                    'type': 'update.mirror',
                                    'data': 'Content Uploaded Successfully '
                                }
                            )
                        
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
                        
                        await channel_layer.send(
                                mirror.get("websocket_channel_name"),
                                {
                                    'type': 'update.mirror',
                                    'data': {'service':'GetMyContents','data':content_list},
                                }
                            )

                    else:
                        print("Mirror not found")
                else:
                    print("Site not found")
                

                # Optionally delete the local file after upload
                os.remove(file_path)

        elif service == 'GetMyMirrors':
            # print
    
            required_fields = ['site_id']
            # print('request data ', text_data_json)
            if not all(field in text_data_json for field in required_fields):
                await self.channel_layer.group_send(
                    "controller_group",
                    {
                        'type': 'update.mirror',
                        'data': 'Required fields - site_id'
                    }
                )
            else:
                site_id = text_data_json['site_id']
                # site = sites_collection.find_one({'_id':site_id})
                MyMirrors = mirror_collection.find({'administrator_id':ObjectId(administrator_id), 'site_id':ObjectId(site_id)})

                MyMirrors_list = []
                for mirror in MyMirrors:
                    if '_id' in mirror:
                        mirror['_id'] = str(mirror['_id'])
                    if 'administrator_id' in mirror:
                        mirror['administrator_id'] = str(mirror['administrator_id'])
                    if 'site_id' in mirror:
                        mirror['site_id'] = str(mirror['site_id'])
                    MyMirrors_list.append(mirror)

                # print(MyMirrors_list)

                await self.channel_layer.group_send(
                        "controller_group",
                        {
                            'type': 'update.mirror',
                            'data': MyMirrors_list
                        }
                    )
        
        elif service == 'GetMySites':
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

            # print(MySites_list)
            await self.channel_layer.group_send(
                    "controller_group",
                    {
                        'type': 'update.controller',
                        'data': MySites_list
                    }
                )
            
        elif service == 'GetMyContents':

            required_fields = ['site_id', 'mirror_id']
            # print('request data ', text_data_json)
            if not all(field in text_data_json for field in required_fields):
                await self.channel_layer.group_send(
                    "controller_group",
                    {
                        'type': 'update.mirror',
                        'data': 'Required fields - site_id, mirror_id'
                    }
                )
            else:


                site_id = text_data_json['site_id']
                mirror_id = text_data_json['mirror_id']
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

                await self.channel_layer.group_send(
                        "controller_group",
                        {
                            'type': 'update.controller',
                            'data': MyContent_list
                        }
                    )
            
        elif service == 'AddSite':
            required_fields = ['site_description','site_name']
            # print(text_data_json)
            if not all(field in text_data_json for field in required_fields):
                await self.channel_layer.group_send(
                    "controller_group",
                    {
                        'type': 'update.mirror',
                        'data': 'Required fields - mirror_name, content , content_title, site_name'
                    }
                )
            else:
                site_name = text_data_json['site_name']
                site_description = text_data_json['site_description']

                # MySites = sites_collection.find({'administrator_id':administrator_id})
                sites_collection.insert_one({
                    "site_name":site_name,
                    "site_description":site_description,
                    "administrator_id":administrator_id
                })
                await self.channel_layer.group_send(
                        "controller_group",
                        {
                            'type': 'update.controller',
                            'data': 'ok'
                        }
                    )
                
        elif service == 'AddMirror':
            required_fields = ['mirror_name','mirror_description','username','password', 'site_id','height','width']
            # print(text_data_json)
            if not all(field in text_data_json for field in required_fields):
                await self.channel_layer.group_send(
                    "controller_group",
                    {
                        'type': 'update.mirror',
                        'data': "Required fields - 'mirror_name','mirror_description','username','password', 'site_id', 'height, width"
                    }
                )
            else:
                mirror_name = text_data_json['mirror_name']
                mirror_description = text_data_json['mirror_description']
                username = text_data_json['username']
                password = text_data_json['password']
                site_id = text_data_json['site_id']
                mirror_height = text_data_json['height']
                mirror_width = text_data_json['width']

                # MySites = sites_collection.find({'administrator_id':administrator_id})
                mirror_collection.insert_one({
                    "username":username,
                    "password":password,
                    "administrator_id":administrator_id,
                    "mirror_name":mirror_name,
                    "mirror_description":mirror_description,
                    "site_id":ObjectId(site_id),
                    "mirror_height":mirror_height,
                    "mirror_width":mirror_width,
                })
                await self.channel_layer.group_send(
                        "controller_group",
                        {
                            'type': 'update.controller',
                            'data': 'ok'
                        }
                    )
                

        else:
            await self.channel_layer.group_send(
                    "controller_group",
                    {
                        'type': 'update.mirror',
                        'data': 'No Such Service Available at this Moment.'
                    }
                )

            
        
        # self.send(text_data="welcome to the service update mirror")
        
            # print('requested service',service)
            

    # async def send_ok(mirror_id):

    #     channel_layer = get_channel_layer()
       
        # print('requested service',service)
      # Define the message handler for 'chat.message'
        
    
    async def update_mirror(self, event):
        data = event['data']

        # Send the message back to WebSocket client
        await self.send(text_data=json.dumps({
            'data': data
        }))
    
    async def update_controller(self, event):
        data = event['data']

        # Send the message back to WebSocket client
        await self.send(text_data=json.dumps({
            'data': data
        }))

class MirrorConsumer(AsyncWebsocketConsumer):
    # this consumer is to handle all the mirror logins

    # def __init__(self, *args, **kwargs):
    #     self.mirror = {}
      # Define the message handler for 'chat.message'
    async def update_mirror(self, event):
        data = event['data']

        # Send the message back to WebSocket client
        await self.send(text_data=json.dumps({
            'data': data
        }))


    async def connect(self):
        # self.uname = "amal"# Accept the WebSocket connection
        query_string = self.scope['query_string'].decode('utf-8')
        params = dict(qc.split("=") for qc in query_string.split("&"))
        client_key = params.get('key') 


        if mirror_collection.find_one({'mirror_login_return_key':client_key}):
            
        # if client_key ==  "key":
            # print(self.channel_name)
            self.mirror = mirror_collection.find_one({'mirror_login_return_key':client_key})
            # Define the filter for which document to update
            filter = {'_id': self.mirror.get("_id")}

            # Define the update operation
            update = {
                '$set': {'websocket_channel_name': self.channel_name}
            }

            # Perform the update
            result = mirror_collection.update_one(filter, update)

            # Check if the update was successful
            if result.modified_count > 0:
                print("Document updated successfully.")
            else:
                print("No document was updated.")
            await self.accept()

            await self.channel_layer.group_add("mirror_group", self.channel_name)

            # await self.channel_layer.group_send(
            #         "mirror_group",
            #         {
            #             'type': 'update.mirror',
            #             'data': {'service':'welcome','data':'Welcome to MirrorLink Server, Now you can Start Your Mirror'},
            #         }
            #     )
            contents = content_collection.find({'mirror_id':self.mirror.get('_id')})
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

            await self.channel_layer.group_send(
                    "mirror_group",
                    {
                        'type': 'update.mirror',
                        'data': {'service':'GetMyContents','data':content_list},
                    }
                )
        else:
            self.close()

        
    async def disconnect(self, code):
        return await super().disconnect(code)
    
    async def receive(self, text_data):
        
        text_data_json = json.loads(text_data)
        service = text_data_json['service']
        # print(self.mirror.get("username"))
        if service == "GetMyContents":
            contents = content_collection.find({'mirror_id':self.mirror.get('_id')})
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

            await self.channel_layer.group_send(
                    "mirror_group",
                    {
                        'type': 'update.mirror',
                        'data': {'service':'GetMyContents','data':content_list},
                    }
                )
        # print(self.mirror.get("username"))