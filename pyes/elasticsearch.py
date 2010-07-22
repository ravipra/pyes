__author__ = 'Alberto Paro, Robert Eanes, Matt Dennewitz'
__all__ = ['ElasticSearch']
__version__ = (0, 0, 4)

try:
    # For Python < 2.6 or people using a newer version of simplejson
    import simplejson as json
except ImportError:
    # For Python >= 2.6
    import json
    
import urllib3
from urlparse import urlsplit
from urllib import urlencode
import logging
from random import randint
import copy
from datetime import date, datetime
from query import Query

class ESJsonEncoder(json.JSONEncoder):
    def default(self, value):
        """Convert rogue and mysterious data types.
        Conversion notes:
        
        - ``datetime.date`` and ``datetime.datetime`` objects are
        converted into datetime strings.
        """

        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%dT%H:%M:%S")
        elif isinstance(value, date):
            dt = datetime(value.year, value.month, value.day, 0, 0, 0)
            return dt.strftime("%Y-%m-%dT%H:%M:%S")
        else:
            # use no special encoding and hope for the best
            return value
        
class ElasticSearch(object):
    """
    ElasticSearch connection object.
    """
    
    def __init__(self, server, debug=False, tracefile=None, timeout=5.0):
        """
        Init a elasticsearch object
        
        server: the server name, it can be a list of servers
        debug: if the calls must be debugged
        tracefile: name of the log file
        timeout: timeout for a call

        """
        self.timeout = timeout
        
        if isinstance(server, (str, unicode)):
            self.servers = [server]
        else:
            self.servers = server
        self.logger = None
        if debug:
            self.debug = debug
            self.tracefile = tracefile
            self.logger = logging.getLogger("elasticsearch")
            self.logger.setLevel(logging.DEBUG)
        else:
            self.logger = logging.getLogger("elasticsearch")
            self.logger.setLevel(logging.WARNING)
            
        self._init_connections()
    
    def _init_connections(self):
        """
        Create initial connection pool
        """
        self.connection_pool = []
        for server in self.servers:
            scheme, netloc, path, query, fragment = urlsplit(server)
            netloc = netloc.split(':')
            host = netloc[0]
            if len(netloc) == 1:
                host, port = netloc[0], None
            else:
                host, port = netloc
            self.connection_pool.append((server, urllib3.HTTPConnectionPool(host, port)))

    def get_connection(self):
        """
        Get a connection from a pool of connections
        """
        num = len(self.connection_pool)
        if num==1:
            return self.connection_pool[0]
        return self.connection_pool[randint(0,num)]

    def _send_request(self, method, path, body="", querystring_args={}):
        if not path.startswith("/"):
            path = "/"+path
        if querystring_args:
            path = "?".join([path, urlencode(querystring_args)])

        if body:
            body = self._prep_request(body)
        conn = self.get_connection()
        self.logger.debug("making %s request to path: %s%s with body: %s" % (method, conn[0], path, body))
        print "curl -X%s %s%s -d '%s'" % (method, conn[0], path, body)
        response = conn[1].urlopen(method, path, body)
        http_status = response.status
        self.logger.debug("response status: %s" % http_status)
        #self.logger .debug("got response %s" % response.data)
        return self._prep_response(response.data)
    
    def _make_path(self, path_components):
        """
        Smush together the path components. Empty components will be ignored.
        """
        path_components = [str(component) for component in path_components if component]
        path = '/'.join(path_components)
        if not path.startswith('/'):
            path = '/'+path
        return path
    
    def _prep_request(self, body):
        """
        Encodes body as json.
        """
        return json.dumps(body, cls=ESJsonEncoder)
        
    def _prep_response(self, response):
        """
        Parses json to a native python object.
        """
        return json.loads(response)
        
    def _query_call(self, query_type, query, indexes=['_all'], doc_types=[], **query_params):
        """
        This can be used for search and count calls.
        These are identical api calls, except for the type of query.
        """
        querystring_args = query_params
        body = query
        if isinstance(query, Query):
            body = query.q
        path = self._make_path([','.join(indexes), ','.join(doc_types),query_type])
        response = self._send_request('GET', path, body, querystring_args)
        return response

    #---- Admin commands
    def status(self, indexes=None):
        """
        Retrieve the status of one or more indices
        """
        if indexes is None:
            indexes = ['_all']
        if isinstance(indexes, (str, unicode)):
            path = self._make_path([indexes, '_status'])
        else: 
            path = self._make_path([','.join(indexes), '_status'])
        return self._send_request('GET', path)

    def create_index(self, index, settings=None):
        """
        Creates an index with optional settings.
        Settings must be a dictionary which will be converted to JSON.
        Elasticsearch also accepts yaml, but we are only passing JSON.
        """
        return self._send_request('PUT', index, settings)
        
    def delete_index(self, index):
        """
        Deletes an index.
        """
        return self._send_request('DELETE', index)
        
    def flush(self, indexes=None, refresh=None):
        """
        Flushes one or more indices (clear memory)
        """
        if indexes is None:
            indexes = ['_all']
        if isinstance(indexes, (str, unicode)):
            path = self._make_path([indexes, '_flush'])
        else: 
            path = self._make_path([','.join(indexes), '_flush'])
        args = {}
        if refresh is not None:
            args['refresh'] = refresh
        return self._send_request('POST', path, querystring_args=args)

    def refresh(self, indexes=None):
        """
        Refresh one or more indices
        """
        if indexes is None:
            indexes = ['_all']
        path = self._make_path([','.join(indexes), '_refresh'])
        return self._send_request('POST', path)
        
    def optimize(self, indexes=None, **args):
        """
        Optimize one ore more indices
        """
        if indexes is None:
            indexes = ['_all']
        path = self._make_path([','.join(indexes), '_optimize'])
        return self._send_request('POST', path, querystring_args=args)

    def gateway_snapshot(self, indexes=None):
        """
        Gateway snapshot one or more indices
        """
        if indexes is None:
            indexes = ['_all']
        path = self._make_path([','.join(indexes), '_gateway', 'snapshot'])
        return self._send_request('POST', path)

    def put_mapping(self, doc_type, mapping, indexes=None):
        """
        Register specific mapping definition for a specific type against one or more indices.
        """
        if indexes is None:
            indexes = ['_all']
        path = self._make_path([','.join(indexes), doc_type,"_mapping"])
        return self._send_request('PUT', path, mapping)

    #--- cluster
    def cluster_health(self, indexes=None, level="cluster", wait_for_status=None, 
               wait_for_relocating_shards=None, timeout=30):
        """
        Request Parameters

        The cluster health API accepts the following request parameters:
        - level:                Can be one of cluster, indices or shards. Controls the details 
                                level of the health information returned. Defaults to cluster.
        - wait_for_status       One of green, yellow or red. Will wait (until the timeout provided) 
                                until the status of the cluster changes to the one provided. 
                                By default, will not wait for any status.
        - wait_for_relocating_shards     A number controlling to how many relocating shards to 
                                         wait for. Usually will be 0 to indicate to wait till 
                                         all relocation have happened. Defaults to not to wait.
        - timeout       A time based parameter controlling how long to wait if one of the 
                        wait_for_XXX are provided. Defaults to 30s.
        """
        path = self._make_path(["_cluster", "health"])
        mapping = {}
        if level!="cluster":
            if level not in ["cluster", "indices", "shards"]:
                raise ValueError("Invalid level: %s"%level)
            mapping['level'] = level
        if wait_for_status:
            if wait_for_status not in ["cluster", "indices", "shards"]:
                raise ValueError("Invalid wait_for_status: %s"%wait_for_status)
            mapping['wait_for_status'] = wait_for_status
            
            mapping['timeout'] = "%ds"%timeout
        return self._send_request('GET', path, mapping)

    def cluster_state(self):
        """
        Retrieve the cluster state
        """
        path = self._make_path(["_cluster", "state"])
        return self._send_request('GET', path)

    def cluster_nodes(self, nodes = None):
        """
        Retrieve the node infos
        """
        parts = ["_cluster", "nodes"]
        if nodes:
            parts.append(",".join(nodes))
        path = self._make_path(parts)
        return self._send_request('GET', path)

    def index(self, doc, index, doc_type, id=None, force_insert=False):
        """
        Index a typed JSON document into a specific index and make it searchable.
        """
        if force_insert:
            querystring_args = {'opType':'create'}
        else:
            querystring_args = {}
            
        if id is None:
            request_method = 'POST'
        else:
            request_method = 'PUT'
        path = self._make_path([index, doc_type, id])
        return self._send_request(request_method, path, doc, querystring_args)
        
    def delete(self, index, doc_type, id):
        """
        Delete a typed JSON document from a specific index based on its id.
        """
        path = self._make_path([index, doc_type, id])
        return self._send_request('DELETE', path)
        
    def get(self, index, doc_type, id):
        """
        Get a typed JSON document from an index based on its id.
        """
        path = self._make_path([index, doc_type, id])
        return self._send_request('GET', path)
        
    def search(self, query, indexes=None, doc_types=None, **query_params):
        """
        Execute a search query against one or more indices and get back search hits.
        query must be a dictionary or a Query object that will convert to Query DSL
        TODO: better api to reflect that the query can be either 'query' or 'body' argument.
        """
        if indexes is None:
            indexes = ['_all']
        if doc_types is None:
            doc_types = []
#        if body:
#            body.update(query_params)
#            query_params = {}
        return self._query_call("_search", query, indexes, doc_types, **query_params)
        
    def count(self, query, indexes=None, doc_types=None, **query_params):
        """
        Execute a query against one or more indices and get hits count.
        """
        if indexes is None:
            indexes = ['_all']
        if doc_types is None:
            doc_types = []
        if isinstance(query, Query):
            query = query.count()
        return self._query_call("_count", query, indexes, doc_types, **query_params)
                    
    def terms(self, fields, indexes=None, **query_params):
        """
        Extract terms and their document frequencies from one or more fields.
        The fields argument must be a list or tuple of fields.
        For valid query params see: 
        http://www.elasticsearch.com/docs/elasticsearch/rest_api/terms/
        """
        if indexes is None:
            indexes = ['_all']
        path = self._make_path([','.join(indexes), "_terms"])
        query_params['fields'] = ','.join(fields)
        return self._send_request('GET', path, querystring_args=query_params)
    
    def morelikethis(self, index, doc_type, id, fields, **query_params):
        """
        Execute a "more like this" search query against one or more fields and get back search hits.
        """
        path = self._make_path([index, doc_type, id, '_mlt'])
        query_params['fields'] = ','.join(fields)
        return self._send_request('GET', path, querystring_args=query_params)        

    def get_query(self):
        """
        Return a Query Object
        """
        return Query(_conn=self)
