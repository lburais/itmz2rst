# ###################################################################################################################################################
# Filename:     onenote.py
# 
# - Author:     [Laurent Burais](mailto:lburais@cisco.com)
# - Release:
# - Date:
#
# Graph Explorer:
#   https://developer.microsoft.com/fr-fr/graph/graph-explorer
#
# ###################################################################################################################################################
# todo
#
#   1. clear: encoding for resource.filename vs. os.walk
#   2. recombine object+image+link (shared from iPad)
#   3. recombine image+object+link (shared from Mac)
#
# ###################################################################################################################################################

import json
import requests
import re
import os
import sys
import shutil

from datetime import datetime as dt
from bs4 import BeautifulSoup

# pip3 install pandas
import pandas as pd

from mytools import *

ME = 'https://graph.microsoft.com/v1.0/users/laurent@burais.fr/onenote'

EXCEPT_HANDLING = False

# ###################################################################################################################################################
# CATALOG
# ###################################################################################################################################################

def catalog( token ): 

    return pd.concat( [ pd.DataFrame( {'source': ['onenote'], 'title': ['All Notebooks'], 'onenote_self': [None] } ),
                        read( token=token, get='catalog' )
                      ], ignore_index = True )

# ###################################################################################################################################################
# READ
# ###################################################################################################################################################

def read( token, notebookUrl=None, directory=None, get='notebooks', elements=empty_elements() ): 

    read_elements = elements.copy()
    if directory: _files_directory = os.path.join( directory, 'onenote' )

    try:

        # -------------------------------------------------------------------------------------------------------------------------------------------
        # process url
        # -------------------------------------------------------------------------------------------------------------------------------------------
        #                       https://graph.microsoft.com/v1.0/me/onenote/notebooks
        #                       https://graph.microsoft.com/v1.0/me/onenote/notebooks/{id}
        #                       https://graph.microsoft.com/v1.0/me/onenote/sectionGroups
        # sectionGroupsUrl :    https://graph.microsoft.com/v1.0/me/onenote/notebooks/{id}/sectionGroups
        # sectionGroupsUrl :    https://graph.microsoft.com/v1.0/me/onenote/sectionGroups/{id}/sectionGroups
        #                       https://graph.microsoft.com/v1.0/me/onenote/sections
        # sectionsUrl:          https://graph.microsoft.com/v1.0/me/onenote/notebooks/{id}/sections
        # sectionsUrl:          https://graph.microsoft.com/v1.0/me/onenote/sectionGroups/{id}/sections
        # pagesUrl:             https://graph.microsoft.com/v1.0/me/onenote/sections/{id}/pages
        # pagesUrl:             https://graph.microsoft.com/v1.0/me/onenote/pages
        # contentUrl:           https://graph.microsoft.com/v1.0/me/onenote/pages/{id}/content
        # resourceUrl:          https://graph.microsoft.com/v1.0/me/onenote/resources/{id}/content

        def process_url( url ):
            nonlocal read_elements

            try:

                get_elements = empty_elements()
                get_url = url

                page_count = 0
                page_nb = 100

                # what: 'notebooks', 'sectionGroups', 'sections', 'pages', 'content', 'resources
                what = re.search( r'^.*/onenote/(.*)$', url ).group(1).split("/")
                if what[-1] in ['notebooks', 'sectionGroups', 'sections', 'pages', 'content', 'resources']:
                    if what[0] in ['resources']: what = what[0]
                    else: what = what[-1]
                else: what = what[0]

                # id: a-bbb!c-ddd!eee or c-ddd!eee
                identifier = None            
                identifier = re.search( r'^.*/(\d-[\w]+!\d-[\w]+![\w]+).*', url )
                if not identifier: identifier = re.search( r'^.*/(\d-[\w]+![\w]+).*', url )
                if identifier: identifier = identifier.group(1)

                while get_url:
                    myprint( '[{} - {}] {}'.format(what, identifier, get_url), prefix='>' )

                    try:
                        onenote_response = requests.get( get_url, headers={'Authorization': 'Bearer ' + token} )

                        if onenote_response.status_code != requests.codes.ok:
                            # exit because of error
                            myprint( '[{}] {} - {}'.format( onenote_response.status_code, 
                                                            onenote_response.json()['error']['code'], 
                                                            onenote_response.json()['error']['message']) )
                            break
                        else:
                            # .......................................................................................................................
                            # ONENOTE JSON NOTEBOOK | SECTIONGROUP | SECTION | PAGE
                            # .......................................................................................................................

                            if onenote_response.headers['content-type'].split(';')[0] == 'application/json':
                                if 'value' not in onenote_response.json(): onenote_objects = { 'value': [ onenote_response.json() ] }
                                else: onenote_objects = onenote_response.json()

                                onenote_objects = pd.json_normalize(onenote_objects['value'])

                                if ('@odata.nextLink' in onenote_response.json() or page_count > 0) and (len(onenote_objects) > 0):
                                    # paginate
                                    get_url = url + '?$top={}'.format(page_nb) + '&$skip={}'.format(page_count)
                                else:
                                    get_url = None

                            # .......................................................................................................................
                            # ONENOTE TEXT CONTENT
                            # .......................................................................................................................

                            elif onenote_response.headers['content-type'].split(';')[0] == 'text/html':
                                # content
                                identifier = re.search( r'^.*pages/(.*?)/content.*', get_url).group(1) if what == 'content' else None
                                onenote_objects = pd.DataFrame( { 'onenote_id': [identifier], 
                                                                  'onenote_self': [url], 
                                                                  'onenote_content': [onenote_response.text]} )

                                # add resources objects
                                onenote_resources = process_resources( onenote_response.text )
                                if len(onenote_resources) > 0:
                                    myprint( 'adding {} resources'.format(len(onenote_resources)), prefix='...' )
                                    onenote_resources['onenote_parentContent.id'] = identifier
                                    onenote_resources['onenote_parentContent.self'] = url
                                    onenote_objects = pd.concat( [ onenote_objects, onenote_resources ], ignore_index = True ) 

                                get_url = None

                            # .......................................................................................................................
                            # ONENOTE BINARY RESOURCE ELEMENT
                            # .......................................................................................................................

                            elif onenote_response.headers['content-type'].split(';')[0] == 'application/octet-stream':
                                # resource
                                def _load_resource( row ):
                                    try:
                                        if not os.path.isdir(os.path.dirname(row['onenote_filename'])): 
                                            os.makedirs(os.path.dirname(row['onenote_filename']))

                                        with open(row['onenote_filename'], 'wb') as fs:
                                            fs.write(onenote_response.content) 

                                        myprint( '{}: {} bytes'.format( row['onenote_filename'], 
                                                                        os.path.getsize(row['onenote_filename']) 
                                                                      ), prefix='...' )

                                        row['onenote_file_loaded'] = True
                                        row['onenote_file_size'] = os.path.getsize(row['onenote_filename'])
                                        row['onenote_file_date'] = dt.fromtimestamp(os.path.getmtime(row['onenote_filename']))
                                    except:
                                        exc_type, exc_obj, exc_tb = sys.exc_info()
                                        myprint( 'error [{} - {}] at line {}'.format(exc_type, exc_obj, exc_tb.tb_lineno), prefix='###')
                                    return row

                                cond = read_elements['onenote_what'].isin(['resources'])
                                cond &= read_elements['onenote_resourceUrl'] == url
                                onenote_objects = read_elements[cond].copy()
                                read_elements = read_elements[~cond]

                                onenote_objects = onenote_objects.apply( _load_resource, axis='columns' )

                                get_url = None

                            # .......................................................................................................................
                            # ELSE
                            # .......................................................................................................................

                            else:
                                # exit because unknown content-type 
                                myprint( onenote_response.headers )
                                break

                            # .......................................................................................................................
                            # ONENOTE ELEMENTS
                            # .......................................................................................................................

                            if 'onenote_what' in onenote_objects: onenote_objects.loc[onenote_objects['onenote_what'].isna(), 'onenote_what'] = what
                            else: onenote_objects['onenote_what'] = what

                            col_list = {}
                            for col in onenote_objects.columns.to_list():
                                if 'onenote_' not in col: col_list[col] = 'onenote_{}'.format(col)
                            onenote_objects.rename( columns=col_list, inplace=True )

                            if len(get_elements) >0 : get_elements = pd.concat( [ get_elements, onenote_objects ], ignore_index=True )
                            else: get_elements = onenote_objects.copy()

                            page_count += len(onenote_objects)

                            del onenote_objects

                    except:
                        exc_type, exc_obj, exc_tb = sys.exc_info()
                        fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
                        myprint("Get error [{} - {}] at line {} in {}.".format(exc_type, exc_obj, exc_tb.tb_lineno, fname), prefix='###')
                        break

                get_elements.drop_duplicates( inplace=True )

                myprint( '{} {} loaded'.format(len(get_elements), what), prefix='...' )

                if len(get_elements) > 0:

                    # recursive
                    if identifier:
                        for u in ['onenote_sectionGroupsUrl', 'onenote_sectionsUrl', 'onenote_pagesUrl']:
                            if u in get_elements:
                                get_elements[(~get_elements[u].isna())][u].apply( lambda x: process_url(x) )

                    # content
                    # for u in ['onenote_contentUrl']:
                    #     if u in get_elements:
                    #         get_elements[(~get_elements[u].isna())][u].apply( lambda x: process_url(x) )

                    # concat 
                    if len(read_elements) > 0: read_elements = pd.concat([read_elements, get_elements], ignore_index=True)
                    else: read_elements = get_elements.copy()

            except:
                exc_type, exc_obj, exc_tb = sys.exc_info()
                fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
                myprint("Url error [{} - {}] at line {} in {}.".format(exc_type, exc_obj, exc_tb.tb_lineno, fname), prefix='###')
                if not EXCEPT_HANDLING: raise

        # -------------------------------------------------------------------------------------------------------------------------------------------
        # process resources
        # -------------------------------------------------------------------------------------------------------------------------------------------

        def process_resources( content ):

            resources_elements = pd.DataFrame()

            soup = BeautifulSoup(content, features="html.parser")

            # objects
            # -------
            # <object 
            # data="https://graph.microsoft.com/v1.0/users('laurent@burais.fr')/onenote/resources/0-8a9f130df6d87945a8099be6b6d2be82!1-34CFFB16AE39C6B3!335924/$value" 
            # data-attachment="SEJOUR BURAIS 007-IND-M-22.pdf" 
            # type="application/pdf">
            # </object>

            for tag in soup.select("object[data-attachment]"): 
                identifier = re.search( r'^.*resources/(.*?)/\$value', tag['data']).group(1)
                filename = identifier.split('!')
                filename.reverse()
                filename = os.path.join( _files_directory, os.path.sep.join(filename), tag['data-attachment'] )
                resources_elements = pd.concat( [ resources_elements,
                                                  pd.DataFrame( { 'onenote_what': ['resources'],
                                                                  'onenote_resource_type': ['object'],
                                                                  'onenote_title': [tag['data-attachment']],
                                                                  'onenote_id': [identifier],
                                                                  'onenote_filename': [filename],
                                                                  'onenote_resourceUrl': [tag['data'].replace('$value', 'content')] } ),
                                                ], ignore_index = True ) 

            # filename:

            # images
            # ------
            # <img 
            # alt="bla bla bla"
            # data-fullres-src="https://graph.microsoft.com/v1.0/users('laurent@burais.fr')/onenote/resources/0-158d4dc3eb09c647b6cb9c4759dc3f69!1-34CFFB16AE39C6B3!335924/$value" 
            # data-fullres-src-type="image/png" 
            # data-id="2f8fe6dc-10b8-c046-ba5b-c6ccf2c8884a" 
            # data-index="2" 
            # data-options="printout" 
            # data-src-type="image/png" 
            # height="842" 
            # src="https://graph.microsoft.com/v1.0/users('laurent@burais.fr')/onenote/resources/0-158d4dc3eb09c647b6cb9c4759dc3f69!1-34CFFB16AE39C6B3!335924/$value" 
            # width="595"
            # />

            for tag in soup.select('img[src]'):
                name = re.search( r'^.*resources/(.*?)!', tag['src']).group(1) + '.' + tag['data-src-type'].replace('image/', '')
                identifier = re.search( r'^.*resources/(.*?)/\$value', tag['src']).group(1)
                filename = identifier.split('!')
                filename.reverse()
                filename = os.path.join( _files_directory, os.path.sep.join(filename), name )
                resources_elements = pd.concat( [ resources_elements,
                                                  pd.DataFrame( { 'onenote_what': ['resources'],
                                                                  'onenote_resource_type': ['image'],
                                                                  'onenote_title': [name],
                                                                  'onenote_id': [identifier],
                                                                  'onenote_filename': [filename],
                                                                  'onenote_resourceUrl': [tag['src'].replace('$value', 'content')] } ),
                                                ], ignore_index = True ) 

            for tag in soup.select('img[data-fullres-src]'):
                name = re.search( r'^.*resources/(.*?)!', tag['data-fullres-src']).group(1) + '.' + tag['data-src-type'].replace('image/', '')
                identifier = re.search( r'^.*resources/(.*?)/\$value', tag['data-fullres-src']).group(1)
                filename = identifier.split('!')
                filename.reverse()
                filename = os.path.join( _files_directory, os.path.sep.join(filename), name )
                resources_elements = pd.concat( [ resources_elements,
                                                  pd.DataFrame( { 'onenote_what': ['resources'],
                                                                  'onenote_resource_type': ['fullres'],
                                                                  'onenote_title': [name],
                                                                  'onenote_id': [identifier],
                                                                  'onenote_filename': [filename],
                                                                  'onenote_resourceUrl': [tag['data-fullres-src'].replace('$value', 'content')] } ),
                                                ], ignore_index = True ) 

            return resources_elements

        # -------------------------------------------------------------------------------------------------------------------------------------------


        if get in ['catalog']:
            # catalog
            process_url(ME + '/notebooks')

        else:
            myprint( '', line=True, title='GET ONENOTE {}'.format(get.upper()))
            myprint( notebookUrl )

            if notebookUrl and (notebookUrl not in ['nan', 'None']):
                # one notebook
                process_url(notebookUrl)

            else:
                # all notebook
                process_url(ME + '/notebooks')

                # sectionGroups
                process_url(ME + '/sectionGroups')

                # sections
                process_url(ME + '/sections')

                # pages
                process_url(ME + '/pages')


            if get in ['notebooks', 'content']:
                # content
                if 'onenote_contentUrl' in read_elements:
                    read_elements[(~read_elements['onenote_contentUrl'].isna())]['onenote_contentUrl'].apply( lambda x: process_url(x) )

            if get in ['notebooks', 'resources']:
                # resources
                if 'onenote_resourceUrl' in read_elements:
                    read_elements[(~read_elements['onenote_resourceUrl'].isna())]['onenote_resourceUrl'].apply( lambda x: process_url(x) )

            myprint( 'Nb elements = {}'.format(len(read_elements)) )

        # body

        # read_elements = body( read_elements )

        # normalize

        read_elements = normalize( read_elements )

    except:
        exc_type, exc_obj, exc_tb = sys.exc_info()
        fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
        myprint("Read error [{} - {}] at line {} in {}.".format(exc_type, exc_obj, exc_tb.tb_lineno, fname), prefix='###')
        if not EXCEPT_HANDLING: raise

    return read_elements

# ###################################################################################################################################################
# READ CONTENT
# ###################################################################################################################################################

def read_content( token, elements ): 

    if 'onenote_contentUrl' in elements:
        return elements[(~elements['onenote_contentUrl'].isna())]['onenote_contentUrl'].apply( lambda x: process_url(x) )


# ###################################################################################################################################################
# BODY
# ###################################################################################################################################################

def body( elements ): 

    try:
        myprint( '', line=True, title='GET ONENOTE CONTENTS')

        cond = (~elements['onenote_content'].isna())
        cond &= (elements['onenote_what'].isin(['content']))

        content_elements = elements[cond][['onenote_self', 'onenote_content', 'onenote_attachments']]

        content_elements.rename( columns= { 'onenote_self': 'onenote_contentUrl', 
                                            'onenote_content': 'content_body',
                                            'onenote_attachments': 'content_attachments'
                                            }, inplace=True )

        pages = (elements['onenote_what'].isin(['pages']))
        elements[pages] = pd.merge( elements[pages].drop( columns=['onenote_content', 'onenote_attachments'] ), 
                                    content_elements, 
                                    on='onenote_contentUrl', 
                                    how='left')

        del content_attachments

    except:
        exc_type, exc_obj, exc_tb = sys.exc_info()
        fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
        myprint("Something went wrong [{} - {}] at line {} in {}.".format(exc_type, exc_obj, exc_tb.tb_lineno, fname), prefix='...')

    return elements

# ###################################################################################################################################################
# NORMALIZE
# ###################################################################################################################################################

def normalize( elements ): 

    try:
        # normalized ['source','what','type','id','title','created','modified','author','parent','childs','body','path','slug','resources']

        myprint( '', line=True, title='NORMALIZE ONENOTE')

        # source
        elements['source'] = 'onenote'

        # what
        elements['what'] = elements['onenote_what'] if 'onenote_what' in elements else nan

        # type
        elements['type'] = 'post' 
        elements.loc[elements['what'].isin(['pages']), 'type'] = 'page' 

        # id
        elements['id'] = elements['onenote_id'] if 'onenote_id' in elements else nan

        # title
        elements['title'] = nan
        if 'onenote_title' in elements.columns.to_list(): elements['title'] = elements['onenote_title']
        if 'onenote_displayName' in elements.columns.to_list(): 
            cond = elements['title'].isna()
            elements.loc[cond, 'title'] = elements[cond]['onenote_displayName']

        # dates
        elements['created'] = elements['onenote_createdDateTime'] if 'onenote_createdDateTime' in elements.columns.to_list() else nan
        elements['modified'] = elements['onenote_lastModifiedDateTime'] if 'onenote_lastModifiedDateTime' in elements.columns.to_list() else nan

        # author
        elements['authors'] = nan
        for col in ['onenote_createdBy.user.displayName', 'onenote_lastModifiedBy.user.displayName']:
            if col in elements.columns.to_list():
                cond = ~elements[col].isna()
                elements.loc[cond, 'authors'] = elements[cond][col]

        # parent
        elements['parent'] = elements['onenote_parent'] if 'onenote_parent' in elements else nan

        # childs
        def _set_childs( element ):
            childs = elements[elements['parent'] == element['id'] ]['id']
            if len(childs) > 0: return childs.to_list()
            else: return nan
        elements['childs'] = elements.apply( _set_childs, axis='columns' )

        # body
        # elements['body'] set above

        # path
        # elements['path'] 

        # resources
        # elements['resources'] = elements['onenote_attachments']

        # slug
        elements['slug'] = elements['id'].apply( lambda x: slugify(x) )

        # drop columns

        #elements.drop( columns=[ 'onenote_attachments', 'onenote_parent' ], inplace=True )

        return elements

    except:
        exc_type, exc_obj, exc_tb = sys.exc_info()
        fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
        myprint("Something went wrong [{} - {}] at line {} in {}.".format(exc_type, exc_obj, exc_tb.tb_lineno, fname), prefix='...')
        raise

        return empty_elements()

# ###################################################################################################################################################
# RESOURCES
# ###################################################################################################################################################

def resources( directory, elements ): 

    resources_elements = elements[elements['source'].isin(['onenote'])].copy()
    _files_directory = os.path.join( directory, 'onenote' )

    try:
        # -------------------------------------------------------------------------------------------------------------------------------------------
        # get resources
        # -------------------------------------------------------------------------------------------------------------------------------------------

        def _get_resource( row ):

            soup = BeautifulSoup(row['onenote_content'], features="html.parser")

            #resources = pd.DataFrame( { 'onenote_what': [ 'attachment', 'onenote_self': [None] ]})

            empty = empty_resource()

            empty['parent'] = row['onenote_id']
            if ('onenote_createdDateTime' in row) and (row['onenote_createdDateTime'] == row['onenote_createdDateTime']): 
                empty['date']  = row['onenote_createdDateTime']
            if ('onenote_lastModifiedDateTime' in row) and (row['onenote_lastModifiedDateTime'] == row['onenote_lastModifiedDateTime']): 
                empty['date']  = row['onenote_lastModifiedDateTime']
            
            # objects
            # -------
            # <object 
            # data="https://graph.microsoft.com/v1.0/users('laurent@burais.fr')/onenote/resources/0-8a9f130df6d87945a8099be6b6d2be82!1-34CFFB16AE39C6B3!335924/$value" 
            # data-attachment="SEJOUR BURAIS 007-IND-M-22.pdf" 
            # type="application/pdf">
            # </object>

            for tag in soup.select("object[data-attachment]"): 
                resource = dict(empty)
                resource['name'] = re.search( r'^.*resources/(.*?)!', tag['data']).group(1) + '_' + tag['data-attachment']
                resource['name'] = tag['data-attachment']
                resource['url'] = tag['data']
                resource['type'] = 'object'

                resources += [ resource ]

            # images
            # ------
            # <img 
            # alt="bla bla bla"
            # data-fullres-src="https://graph.microsoft.com/v1.0/users('laurent@burais.fr')/onenote/resources/0-158d4dc3eb09c647b6cb9c4759dc3f69!1-34CFFB16AE39C6B3!335924/$value" 
            # data-fullres-src-type="image/png" 
            # data-id="2f8fe6dc-10b8-c046-ba5b-c6ccf2c8884a" 
            # data-index="2" 
            # data-options="printout" 
            # data-src-type="image/png" 
            # height="842" 
            # src="https://graph.microsoft.com/v1.0/users('laurent@burais.fr')/onenote/resources/0-158d4dc3eb09c647b6cb9c4759dc3f69!1-34CFFB16AE39C6B3!335924/$value" 
            # width="595"
            # />

            for tag in soup.select('img[src]'):
                resource = dict(empty)
                resource['name'] = re.search( r'^.*resources/(.*?)!', tag['src']).group(1) + '.' + tag['data-src-type'].replace('image/', '')
                resource['url'] = tag['src']
                resource['type'] = 'image'

                resources += [ resource ]

                resource = dict(empty)
                resource['name'] = re.search( r'^.*resources/(.*?)!', tag['data-fullres-src']).group(1) + '.' + tag['data-src-type'].replace('image/', '')
                resource['url'] = tag['data-fullres-src']
                resource['type'] = 'fullres'

                resources += [ resource ]

            for resource in resources:
                path = resource['parent'].split('!')
                path.reverse()
                resource['filename'] = os.path.join( _files_directory, 
                                                    os.path.sep.join(path),
                                                    resource['name'] )


            return json.dumps( resources )

            #if len(resources) > 0: return json.dumps( resources )
            #else: return nan

        # -------------------------------------------------------------------------------------------------------------------------------------------

        myprint( '', line=True, title='GET ONENOTE RESOURCES')
        
        if 'onenote_attachments' not in elements:
            elements['onenote_attachment'] = nan

        cond = (~elements['onenote_content'].isna())

        myprint( 'Processing {} attachments out of {} contents'. format( len(elements[cond]), 
                                                                         len(elements[(~elements['onenote_content'].isna())])))

        elements.loc[cond, 'onenote_attachments'] = _elements[cond].apply(_get_resource, axis='columns')

        cond = (~_elements['onenote_content'].isna())
        cond &= (_elements['onenote_attachments'].isna())

        if len(_elements[cond]) > 0: myprint( 'missing {} attachments'. format(len(_elements[cond])), prefix="...")

    except:
        exc_type, exc_obj, exc_tb = sys.exc_info()
        fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
        myprint("Something went wrong [{} - {}] at line {} in {}.".format(exc_type, exc_obj, exc_tb.tb_lineno, fname), prefix='...')

    return elements

# ###################################################################################################################################################
# NEXT
# ###################################################################################################################################################

def next():

    try:
    
        # -------------------------------------------------------------------------------------------------------------------------------------------
        # set parent
        # -------------------------------------------------------------------------------------------------------------------------------------------

        myprint( '', line=True, title='SET ONENOTE PARENT' )

        _elements['onenote_parent'] = nan

        if 'onenote_parentNotebook.id' in _elements.columns.to_list():
            _elements.loc[~_elements['onenote_parentNotebook.id'].isna(), 'onenote_parent'] = _elements['onenote_parentNotebook.id']
        if 'onenote_parentSectionGroup.id' in _elements.columns.to_list():
            _elements.loc[~_elements['onenote_parentSectionGroup.id'].isna(), 'onenote_parent'] = _elements['onenote_parentSectionGroup.id']
        if 'onenote_parentSection.id' in _elements.columns.to_list():
            _elements.loc[~_elements['onenote_parentSection.id'].isna(), 'onenote_parent'] = _elements['onenote_parentSection.id']

        # -------------------------------------------------------------------------------------------------------------------------------------------
        # reorganize elements
        # -------------------------------------------------------------------------------------------------------------------------------------------
        # merge elements

        myprint( '', line=True, title='REORGANIZE ONENOTE ELEMENTS' )

        def _find_page( row ):
            cond = (_elements['onenote_self'].str.contains("/pages/"))
            cond &= (_elements['tmp_name'].isin([row['tmp_name']]))
            cond &= (_elements['onenote_parent'].isin([row['onenote_id']]))
            found_pages = _elements[cond]
            if len(found_pages) > 0:
                # it is a match
                for index, found_page in found_pages.iterrows():
                    if found_page['onenote_content'] == found_page['onenote_content']: 
                        if row['onenote_content'] != row['onenote_content']: row['onenote_content'] = found_page['onenote_content']
                        else: row['onenote_content'] += found_page['onenote_content']
                    if found_page['onenote_attachments'] == found_page['onenote_attachments']: 
                        if row['onenote_attachments'] != row['onenote_attachments']: row['onenote_attachments'] = found_page['onenote_attachments']
                        else: row['onenote_attachments'] = json.dumps( json.loads(row['onenote_attachments']) + json.loads(found_page['onenote_attachments']) )
                row['tmp_found'] = found_pages['onenote_id'].to_list()
            return row

        _elements['tmp_name'] = nan
        if 'onenote_displayName' in _elements.columns.to_list():
            _elements['tmp_name'] = _elements['onenote_displayName']
        if 'onenote_title' in _elements.columns.to_list():
            cond = ~_elements['onenote_title'].isna()
            cond &= _elements['tmp_name'].isna()
            _elements.loc[cond, 'tmp_name'] = _elements['onenote_title']

        cond = (~_elements['onenote_self'].str.contains("/pages/"))
        _elements['tmp_found'] = nan
        _elements[cond] = _elements[cond].apply(_find_page, axis='columns')

        cond = (~_elements['tmp_found'].isna())
        myprint( 'merged {} contents'. format(len(_elements[cond])), prefix="...")

        _elements['onenote_merged'] = False
        pages = list(dict.fromkeys([x for xs in _elements[cond]['tmp_found'].drop_duplicates().to_list() for x in xs]))
        cond = (_elements['onenote_id'].isin(pages))
        _elements.loc[cond, 'onenote_merged'] = True

        _elements.drop( columns=['tmp_found', 'tmp_name'], inplace=True)

        # -------------------------------------------------------------------------------------------------------------------------------------------
        # load resources
        # -------------------------------------------------------------------------------------------------------------------------------------------

        def _load_resource( resource ):

            if not resource['filename']:
                myprint("[{}] no filename for {}".format(resource['index'], resource['url']), prefix='  ...')
                return resource

            # test dates to check if load is mandatory
            date_page = resource['date']
            try:
                date_page = dt.strptime(date_page, '%Y-%m-%dT%H:%M:%S.%fZ')
            except:
                date_page = dt.strptime(date_page, '%Y-%m-%dT%H:%M:%SZ')

            try:
                date_file = dt.fromtimestamp(os.path.getmtime( resource['filename'] ))
            except:
                date_file = date_page

            # load file
            if not os.path.isfile(resource['filename']) or (date_file < date_page):

                myprint( '[{}] {}...'.format(resource['index'], resource['url'].replace('$value', 'content')), prefix='>')

                if not os.path.isfile(resource['filename']): myprint( 'missing file', prefix='...' )
                elif (date_file < date_page): myprint( 'outdated file', prefix='...' )

                out_dir = os.path.dirname(resource['filename'])

                if not os.path.isdir(out_dir):
                    os.makedirs(out_dir)

                try:
                    data =  requests.get( resource['url'].replace('$value', 'content'), headers={'Authorization': 'Bearer ' + token} )
                    try:
                        iserror = ('error' in data.json())
                        if iserror:
                            myprint( 'error: {} - {}'.format(data.json()['error']['code'], data.json()['error']['message']) )
                    except:
                        iserror = False
                        with open(resource['filename'], 'wb') as fs:
                            fs.write(data.content) 

                        myprint( '[{}] {}: {} bytes'.format( resource['index'], 
                                                                resource['filename'], 
                                                                os.path.getsize(resource['filename']) ), 
                                    prefix='...' )

                        resource['processed'] = True
                except:
                    exc_type, exc_obj, exc_tb = sys.exc_info()
                    myprint( 'error: {}'.format(exc_obj), prefix='...' )
            else:
                    resource['processed'] = True

            return resource

        cond = (~_elements['onenote_attachments'].isna())
        resources = _elements[cond]['onenote_attachments'].apply( lambda x: json.loads(x) )
        if len(resources) > 0: 
            myprint( '', line=True, title='LOAD ONENOTE RESOURCES')

            resources = resources.apply(pd.Series).stack().reset_index(drop=True).apply(pd.Series)

            nb = len(resources)
            myprint( 'Processing {} resources'. format(nb))
            resources['index'] = range(nb, 0, -1)        
            resources['processed'] = False       
            
            if len(resources) > 0: 
                resources = resources.apply(_load_resource, axis='columns')

            myprint( '.. missing {} resources out of {}'. format(len(resources[resources['processed']==False]), nb))

        # -------------------------------------------------------------------------------------------------------------------------------------------
        # set body
        # -------------------------------------------------------------------------------------------------------------------------------------------

        def _body( element ):
            if element['onenote_content'] and (element['onenote_content'] == element['onenote_content']):

                soup = BeautifulSoup( '<body>' + element['onenote_content'] + '</body>', features="html.parser" )

                # absolute
                # --------
                # <body data-absolute-enabled="true" style="font-family:Calibri;font-size:11pt">
                # <div style="position:absolute;left:48px;top:115px;width:576px">

                for tag in soup.select("body[data-absolute-enabled]"):
                    del tag["data-absolute-enabled"]

                tags = soup.find_all( 'div', style=re.compile("position:absolute") )
                for tag in tags:
                    if (tag["style"].find("position:absolute") != -1):
                        del tag["style"]

                # resize images

                tags = soup.find_all( 'img' )
                for tag in tags:
                    del tag['width']
                    del tag['height']

                    tag['width'] = 1000

                # resize objects

                tags = soup.find_all( 'object' )
                for tag in tags:
                    del tag['width']
                    del tag['height']

                    tag['width'] = 1000

                # stripped

                if len(soup.body.contents) > 0:
                    element['body'] = str( soup.body.contents[0] )
                
                # resources

                # replace url by file
                if element['onenote_attachments'] == element['onenote_attachments']:
                    for resource in json.loads(element['onenote_attachments']):
                        if resource['filename']:
                            element['body'] = element['body'].replace( resource['url'], 
                                                                    resource['filename'].replace( directory, 'static' )
                                                                    )

            return element

        if 'onenote_content' in _elements.columns.to_list():
            myprint( '', line=True, title='SET ONENOTE BODY')

            cond = (~_elements['onenote_content'].isna())
            _elements['body'] = nan
            _elements.loc[cond, 'body'] = _elements[cond].apply( _body, axis='columns' )
        
    except:
        exc_type, exc_obj, exc_tb = sys.exc_info()
        fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
        myprint("Something went wrong [{} - {}] at line {} in {}.".format(exc_type, exc_obj, exc_tb.tb_lineno, fname), prefix='...')

        return empty_elements()

# ###################################################################################################################################################
# WRITE
# ###################################################################################################################################################

def write( directory, token, elements=empty_elements() ): 

    pass

# ###################################################################################################################################################
# CLEAR
# ###################################################################################################################################################

def clear( directory, elements=empty_elements(), all=False ): 

    myprint( '', line=True, title='CLEAR ONENOTE FILES')

    _directory = os.path.join( directory, 'onenote' )

    if all:
        if os.path.isdir(_directory):
            myprint( 'Removing {}...'.format(_directory), prefix='>' )
            shutil.rmtree(_directory)
            os.makedirs(_directory)
    else:
        cond = (~elements['resources'].isna())
        cond &= elements['source'].isin(['onenote'])
        resources = elements[cond]['resources'].apply( lambda x: json.loads(x) )

        if len(resources) > 0: 
            resources = resources.apply(pd.Series).stack().reset_index(drop=True).apply(pd.Series)
            resources = resources['filename'].drop_duplicates()

            myprint( 'Processing {} resources'. format(len(resources)))

            onenote_files = list(dict.fromkeys(resources.to_list()))

            count = 0
            removed = 0
            for root, dirs, files in os.walk(_directory):
                for name in files:
                    if not (os.path.join(root, name) in onenote_files):
                        removed += 1
                        myprint('[{}] removing {} file'.format(removed, os.path.join(root, name)), prefix='>')
                        #os.remove( os.path.join(root, name) )
                    count += 1

            for root, dirs, files in os.walk(_directory, topdown=False):
                for name in dirs:
                    walk = sum([len(files) for r, d, files in os.walk(os.path.join(root, name))])
                    if walk == 0:
                        myprint('removing {} directory'.format(os.path.join(root, name)), prefix='>')
                        #shutil.rmtree(os.path.join(root, name))

            myprint('removed {} out of {} files'.format(removed, count), prefix='...')

