# #####################################################################################################################################################################################################
# Filename:     onenote.py
# 
# - Author:     [Laurent Burais](mailto:lburais@cisco.com)
# - Release:
# - Date:
#
# Graph Explorer:
#   https://developer.microsoft.com/fr-fr/graph/graph-explorer
#
# #####################################################################################################################################################################################################

import json
import requests
import re
import os
import sys
import shutil
import random
import string
import time

from datetime import datetime as dt
import pytz

from xml.etree import ElementTree
from pathvalidate import sanitize_filename
from html.parser import HTMLParser
from fnmatch import fnmatch

from mytools import *

MICROSOFT_GRAPH_URL = 'https://graph.microsoft.com/v1.0'
ALL_NOTEBOOKS = 'All Notebooks'

OK = True
KO = False

# -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
# INDENT_PRINT
# -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

def _indent_print(depth, text):
    print('  ' * depth + text)

def _get_object_date( obj ):
    try:
        obj_date = obj['lastModifiedDateTime'] if 'lastModifiedDateTime' in obj else obj['createdDateTime']
        try:
            obj_date = dt.strptime(obj_date, '%Y-%m-%dT%H:%M:%S.%fZ')
        except:
            obj_date = dt.strptime(obj_date, '%Y-%m-%dT%H:%M:%SZ')
    except:
        obj_date = dt.now(dt.timezone.utc)

    return obj_date


# #####################################################################################################################################################################################################
# ONENOTE
# #####################################################################################################################################################################################################

class ONENOTE:
    # -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
    # __INIT__
    # -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

    def __init__ (self, token, output_directory=None ):

        self.token = token
        if output_directory:
            self.output_directory = output_directory
        else:
            self.output_directory = os.path.join( os.path.dirname(__file__), 'output' )

        self.output_directory = os.path.join( self.output_directory, 'onenote' )

    # -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
    # CATALOG
    # -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

    def catalog( self ):
        notebooks = self._get_json(f'{MICROSOFT_GRAPH_URL}/me/onenote/notebooks')

        # add command to list all local onenote        
        catalog = [ { 'source': 'onenote', 'object': 'onenote', 'name': 'Onenote', 'command': 'list' } ]

        # add command to parse all notebooks        
        catalog += [ { 'source': 'onenote', 'object': 'notebook', 'name': 'All Notebooks', 'command': 'parse' } ] if len(notebooks) > 0 else []

        # add command to parse each notebook      
        for nb in notebooks:
            catalog += [ { 'source': 'onenote', 'object': 'notebook', 'name': nb['displayName'], 'command': 'parse&notebook={}'.format(nb['displayName']) } ]

        return catalog

    # -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
    # LIST
    # -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

    def list( self ):
        catalog = []

        base = len(os.path.normpath(self.output_directory).split(os.sep))

        for root, subdirs, files in os.walk(self.output_directory):
            element = { 'source': 'onenote' }

            element['indent'] = len(os.path.normpath(root).split(os.sep)) - base

            for file in files:
                root_ext = os.path.splitext(file)

                if root_ext[1] in ['.json']:
                    element['object'] = root_ext[0]

                    if element['object'] in ['page']:

                        element['hierarcchy'] = root.split(os.sep)

                        with open(os.path.join(root, file), 'r') as f:
                            f_content = json.load(f)
                            
                            if 'displayName' in f_content: element['name'] = f_content['displayName']
                            elif 'title' in f_content: element['name'] = f_content['title']

                            if 'lastModifiedDateTime' in f_content: element['date'] = f_content['lastModifiedDateTime']
                            else: element['date'] = f_content['createdDateTime']

                            if 'main.html' in files: element['url'] = 'file://' + os.path.join(root, 'main.html')

                            element['data'] = f_content

                            catalog += [ element ]

        return catalog

    # -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
    # PARSE
    # -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

    def parse( self, notebook=None, force=False ):

        if notebook in [ALL_NOTEBOOKS]: notebook = None
        self._download_notebooks( self.output_directory, select=notebook, force=force )
        return []

    # -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
    # GET_JSON
    # -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

    def _get_json(self, url, force=False , indent=0):
        values = []
        next_page = url
        while next_page:
            resp = self._get(next_page, indent=indent)
            if resp:
                if resp.headers['content-type'].split(';')[0] == 'application/json':
                    resp = resp.json()
                    if 'value' not in resp:
                        raise RuntimeError(f'Invalid server response: {resp}')
                    values += resp['value']
                    next_page = resp.get('@odata.nextLink')
                else:
                    _indent_print( indent, f'not a json: {resp.headers["content-type"].split(";")[0]}' )

        return values

    # -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
    # GET
    # -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

    def _get(self, url, indent=0):
        try:
            sec = 0
            while True:
                resp = requests.get( url, headers={'Authorization': 'Bearer ' + self.token['access_token']} )

                if resp.status_code == 429:
                    # We are being throttled due to too many requests.
                    # See https://docs.microsoft.com/en-us/graph/throttling
                    sec = min( [ sec + 20, 60 ] )
                    
                    _indent_print(indent, f'Too many requests, waiting {sec}s and trying again.')
                    time.sleep(sec)
                
                elif resp.status_code == 500:
                    # In my case, one specific note page consistently gave this status
                    # code when trying to get the content. The error was "19999:
                    # Something failed, the API cannot share any more information
                    # at the time of the request."
                    _indent_print(indent, 'Error 500, skipping this page.')
                    return None
                
                elif resp.status_code == 504:
                    _indent_print(indent, 'Request timed out, probably due to a large attachment. Skipping.')
                    return None
                
                else:
                    resp.raise_for_status()
                    return resp
        except:
            return None

    # -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
    # DOWNLOAD_ATTACHMENTS
    # -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

    def _download_attachments(self, content, out_dir, indent=0):
        image_dir = os.path.join( out_dir, 'images' )
        attachment_dir = os.path.join( out_dir, 'attachments' )

        class MyHTMLParser(HTMLParser):
            def handle_starttag(self, tag, attrs):
                self.attrs = {k: v for k, v in attrs}

        def generate_html(tag, props):
            element = ElementTree.Element(tag, attrib=props)
            return ElementTree.tostring(element, encoding='unicode')

        def download_image(tag_match):
            try:
                # <img width="843" height="218.5" src="..." data-src-type="image/png" data-fullres-src="..."
                # data-fullres-src-type="image/png" />
                parser = MyHTMLParser()
                parser.feed(tag_match[0])
                props = parser.attrs
                image_url = props.get('data-fullres-src', props['src'])
                image_type = props.get('data-fullres-src-type', props['data-src-type']).split("/")[-1]
                file_name = ''.join(random.choice(string.ascii_lowercase) for _ in range(10)) + '.' + image_type

                req = self._get(image_url, indent=indent)
                
                if req is None:
                    return tag_match[0]
                img = req.content
                _indent_print(indent, f'Downloaded image of {len(img)} bytes.')

                out_image = os.path.join( image_dir, file_name )

                os.makedirs( image_dir, exist_ok=True )
                with open(out_image, "wb") as f:
                    f.write(img)
                props['src'] = "images/" + file_name
                props = {k: v for k, v in props.items() if 'data-fullres-src' not in k}

                return generate_html('img', props)

            except:
                _indent_print(indent, f'Soemthing wrong with the image.')
                return tag_match[0]


        def download_attachment(tag_match):
            try:
                # <object data-attachment="Trig_Cheat_Sheet.pdf" type="application/pdf" data="..."
                # style="position:absolute;left:528px;top:139px" />
                parser = MyHTMLParser()
                parser.feed(tag_match[0])
                props = parser.attrs
                data_url = props['data']
                file_name = props['data-attachment']

                out_attachment = os.path.join( attachment_dir, file_name )
            
                if os.path.exists( out_attachment ): 
                    _indent_print(indent, f'Attachment {file_name} already downloaded; skipping.')
                else:
                    req = self._get(data_url, indent=indent)

                    if req is None:
                        return tag_match[0]
                    data = req.content
                    _indent_print(indent, f'Downloaded attachment {file_name} of {len(data)} bytes.')

                    os.makedirs( attachment_dir, exist_ok=True )
                    with open(out_attachment, "wb") as f:
                        f.write(data)
                props['data'] = "attachments/" + file_name

                return generate_html('object', props)

            except:
                _indent_print(indent, f'Soemthing wrong with the attachment.')
                return tag_match[0]

        content = re.sub(r"<img .*?\/>", download_image, content, flags=re.DOTALL)
        content = re.sub(r"<object .*?\/>", download_attachment, content, flags=re.DOTALL)

        return content


    # -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
    # FILTER_ITEMS
    # -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

    def _filter_items(self, items, select, name='items'):
        if not select:
            return items, select

        items = [item for item in items
                if fnmatch(item.get('displayName', item.get('title')).lower(), select[0].lower())]

        return items, select[1:]


    # -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
    # DOWNLOAD_NOTEBOOKS
    # -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

    def _download_notebooks(self, path, select=None, force=False , indent=0):
        notebooks = self._get_json(f'{MICROSOFT_GRAPH_URL}/me/onenote/notebooks')
        success = OK

        _indent_print(0, f'Got {len(notebooks)} notebooks.')

        notebooks, select = self._filter_items(notebooks, select, 'notebooks')

        for obj in notebooks:
            obj_name = obj["displayName"]

            # if obj_name in ['Archives']: continue

            obj_dir = os.path.join( path, obj_name)

            out_json = os.path.join( obj_dir, 'notebook.json')

            if force: shutil.rmtree( obj_dir, ignore_errors=True )
            os.makedirs( obj_dir, exist_ok=True )

            _indent_print(indent, '- NOTEBOOK: {} {}'.format( obj_name, '-'*(80-5-len('NOTEBOOK')-len(obj_name)) ) )

            if os.path.isfile( out_json ):
                if dt.utcfromtimestamp(os.path.getmtime(out_json)) > _get_object_date( obj ):
                    _indent_print(indent + 1, 'Skipping notebook {} [{} > {}]'.format( obj_name, 
                                                                                       dt.utcfromtimestamp(os.path.getmtime(out_json)).strftime("%Y-%m-%d %H:%M:%S"), 
                                                                                       _get_object_date( obj ).strftime("%Y-%m-%d %H:%M:%S")))
                    continue

            sections = self._get_json(obj['sectionsUrl'])
            section_groups = self._get_json(obj['sectionGroupsUrl'])

            _indent_print(indent + 1, f'Got {len(sections)} sections and {len(section_groups)} section groups.')

            if self._download_sections(sections, obj_dir, select, force=force, indent=indent + 1) and \
               self._download_section_groups(section_groups, obj_dir, select, force=force, indent=indent + 1):

                with open(out_json, "w", encoding='utf-8') as f:
                    f.write(json.dumps(obj, indent = 4))

            else: success = KO

        return success

    # -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
    # DOWNLOAD_SECTION_GROUPS
    # -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

    def _download_section_groups(self, section_groups, path, select=None, force=False , indent=0):
        section_groups, select = self._filter_items(section_groups, select, 'section groups')
        success = OK

        for obj in section_groups:
            obj_name = obj["displayName"]

            obj_dir = os.path.join( path, obj_name)

            out_json = os.path.join( obj_dir, 'group.json')

            if force: shutil.rmtree( obj_dir, ignore_errors=True )
            os.makedirs( obj_dir, exist_ok=True )

            _indent_print(indent, '- SECTION GROUP: {} {}'.format( obj_name, '-'*(80-5-len('SECTION GROUP')-len(obj_name)) ) )

            if os.path.isfile( out_json ):
                if dt.utcfromtimestamp(os.path.getmtime(out_json)) > _get_object_date( obj ):
                    _indent_print( indent + 1, 'Skipping group {} [{} > {}]'.format( obj_name, 
                                                                                     dt.utcfromtimestamp(os.path.getmtime(out_json)).strftime("%Y-%m-%d %H:%M:%S"), 
                                                                                     _get_object_date( obj ).strftime("%Y-%m-%d %H:%M:%S")))
                    continue

            sections = self._get_json(obj['sectionsUrl'])

            _indent_print(indent + 1, f'Got {len(sections)} sections.')

            if self._download_sections(sections, obj_dir, select, force=force, indent=indent + 1):

                with open(out_json, "w", encoding='utf-8') as f:
                    f.write(json.dumps(obj, indent = 4))

            else: success = KO

        return success

    # -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
    # DOWNLOAD_SECTIONS
    # -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

    def _download_sections(self, sections, path, select=None, force=False , indent=0):
        sections, select = self._filter_items(sections, select, 'sections')
        success = OK

        for obj in sections:
            obj_name = obj["displayName"]

            obj_dir = os.path.join( path, obj_name)

            out_json = os.path.join( obj_dir, 'section.json')

            if force: shutil.rmtree( obj_dir, ignore_errors=True )
            os.makedirs( obj_dir, exist_ok=True )

            _indent_print(indent, '- SECTION: {} {}'.format( obj_name, '-'*(80-5-len('SECTION')-len(obj_name)) ) )

            if os.path.isfile( out_json ):
                if dt.utcfromtimestamp(os.path.getmtime(out_json)) > _get_object_date( obj ):
                    _indent_print( indent + 1, 'Skipping section {} [{} > {}]'.format( obj_name, 
                                                                                       dt.utcfromtimestamp(os.path.getmtime(out_json)).strftime("%Y-%m-%d %H:%M:%S"), 
                                                                                       _get_object_date( obj ).strftime("%Y-%m-%d %H:%M:%S")))
                    continue

            pages = self._get_json( obj['pagesUrl'] + '?pagelevel=true')

            _indent_print(indent + 1, f'Got {len(pages)} pages.')

            if self._download_pages( pages, obj_dir, select, force=force, indent=indent + 1):

                with open(out_json, "w", encoding='utf-8') as f:
                    f.write(json.dumps(obj, indent = 4))

            else: success = KO

        return success

    # -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
    # DOWNLOAD_PAGES
    # -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

    def _download_pages(self, pages, path, select=None, force=False , indent=0):
        pages, select = self._filter_items(pages, select, 'pages')

        pages = sorted([(page['order'], page) for page in pages])
        level_dirs = [None] * 4
        success = OK

        for order, page in pages:
            level = page['level']
            page_title = sanitize_filename(f'{order} {page["title"]}', platform='auto')

            _indent_print(indent, '- PAGE: {} {}'.format( page_title, '-'*(80-5-len('PAGE')-len(page_title)) ) )

            if level == 0:
                page_dir = os.path.join( path, page_title)
            else:
                try:
                    level_dir = next((dop for dop in reversed(level_dirs[:level-1]) if dop is not None), level_dirs[level - 1])
                    page_dir = os.path.join( level_dir, page_title)
                except:
                    print(f'level: {level}, dir: {level_dir}, join: {level_dirs[level - 1]}, level_dirs: {level_dirs}')
                    raise
            level_dirs[level] = page_dir

            success &= self._download_page( page, page_dir, force=force, indent=indent + 1)

        return success

    # -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
    # DOWNLOAD_PAGE
    # -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

    def _download_page(self, page, path, force=False , indent=0):

        out_html = os.path.join( path, 'main.html')
        out_json = os.path.join( path, 'page.json')

        if force: shutil.rmtree( path, ignore_errors=True )
        os.makedirs( path, exist_ok=True )

        if not force and os.path.isfile( out_json ):
            obj = page
            obj_name = page["title"]

            if dt.utcfromtimestamp(os.path.getmtime(out_json)) > _get_object_date( obj ):
                _indent_print( indent + 1, 'Skipping page {} [{} > {}]'.format( obj_name, 
                                                                                dt.utcfromtimestamp(os.path.getmtime(out_json)).strftime("%Y-%m-%d %H:%M:%S"), 
                                                                                _get_object_date( obj ).strftime("%Y-%m-%d %H:%M:%S")))
                return OK

        shutil.rmtree( path, ignore_errors=True )
        os.makedirs( path, exist_ok=True )

        response = self._get(page['contentUrl'], indent=indent)

        if response is not None:
            content = response.text
            _indent_print(indent, f'Got content of length {len(content)}')

            content = self._download_attachments( content, path, indent=indent)

            with open(out_html, "w", encoding='utf-8') as f:
                f.write(content)

            with open(out_json, "w", encoding='utf-8') as f:
                f.write(json.dumps(page, indent = 4))

            return OK
        
        else:
            return KO