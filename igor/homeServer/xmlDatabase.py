import xml.dom
import xpath
import sys
import traceback
import threading
import time
import os
import re

TAG_PATTERN = re.compile('^[a-zA-Z_:][-_:.a-zA-Z0-9]*$')

DOCUMENT="""<data><x>1</x><y>2</y></data>"""

class DBKeyError(KeyError):
	pass
	
class DBParamError(ValueError):
	pass
	
class DBSerializer:
	"""Baseclass with methods to provide a mutex and a condition variable"""
	def __init__(self):	  
		self.lockCount = 0
		self._waiting = {}
		self._callbacks = []
		self._lock = threading.RLock()

	def enter(self):
		"""Enter the critical section for this database"""
		self._lock.acquire()
		self.lockCount += 1
		
	def leave(self):
		self._lock.release()
		
	def __enter__(self):
		self.enter()
		
	def __exit__(self, *args):
		self.leave()

	def registerCallback(self, callback, location):
		with self:
			self.unregisterCallback(callback)
			self._callbacks.append((location, callback))
			
	def unregisterCallback(self, callback):
		with self:
			for i in range(len(self._callbacks)):
				if self._callbacks[i][0] == callback:
					del self._callbacks[i]
					return
			
	def waitLocation(self, location, oldValue=None):
		"""Register that we are interested in the given XPath. Returns the semaphore"""
		if not location in self._waiting:
			self._waiting[location] = [threading.Condition(self._lock), 0]
		if not oldValue is None:
			if oldValue < self._waiting[location][1]:
				print 'DBG waitLocation returning early for', oldValue
				return self._waiting[location][1]
		print 'DBG waitLocation waiting for', location
		self._waiting[location][0].wait()
		if dbapi.LOGGING: print 'waitLocation returned for', location, 'on', self._waiting[location]
		return self._waiting[location][1]
		
	def signalNodelist(self, nodelist):
		"""Wake up clients waiting for the given nodes"""
		for location, cv in self._waiting.items():
			waitnodelist = xpath.find(location, self._doc.documentElement)
			for wn in waitnodelist:
				if wn in nodelist:
					print 'DBG signal for', wn
					cv[1] += 1
					cv[0].notify_all()
					break
		for location, callback in self._callbacks:
			waitnodelist = xpath.find(location, self._doc.documentElement)
			for wn in waitnodelist:
				if wn in nodelist:
					print 'DBG callback for', wn
					callback(wn)	
		
class DBImpl(DBSerializer):
	"""Main implementation of the database API"""
	
	def __init__(self, filename):
		DBSerializer.__init__(self)
		self._terminating = False
		self._domimpl = xml.dom.getDOMImplementation()
		self.filename = filename
		self.tmpCounter = 1
		if os.path.exists(filename):
			self.initialize(filename=filename)
		else:
			self.initialize(xmlstring=DOCUMENT)

	def signalNodelist(self, nodelist):
		with self:
			newFilename = self.filename + '.NEW' + str(self.tmpCounter)
			self.tmpCounter += 1
			self._doc.writexml(open(newFilename, 'w'))
			os.rename(newFilename, self.filename)
			DBSerializer.signalNodelist(self, nodelist)
		
	def getMessageCount(self):
		with self:
			return self.lockCount
		
	def terminate(self):
		with self:
			self._terminating = True
			sys.exit(1)
		
	def is_terminating(self):
		with self:
			return self._terminating
		
	def initialize(self, xmlstring=None, filename=None):
		"""Reset the document to a known value (passed as an XML string"""
		with self:
			if filename:
				self._doc = xml.dom.minidom.parse(filename)
			elif xmlstring:
				self._doc = xml.dom.minidom.parseString(xmlstring)
			else:
				self._doc = self._domimpl.createDocument('', 'root', None)
	
	def echo(self, arg):
		"""Return the argument (for performance testing)"""
		return arg
		
	def getXMLDocument(self):
		"""Return the whole document (as an XML string)"""
		with self:
			rv = self._doc.toprettyxml()
			return rv
		
	def getDocument(self):
		"""Return the whole document (as a DOM element)"""
		with self:
			return self._doc.documentElement
		
	def splitXPath(self, location):
		lastSlashPos = location.rfind('/')
		if lastSlashPos == 0:
			parent = '.'
			child = location[lastSlashPos+1:]
		elif lastSlashPos > 0:
			parent = location[:lastSlashPos]
			child = location[lastSlashPos+1:]
		else:
			parent = '.'
			child = location
		# Test that child is indeed a tag name
		if not TAG_PATTERN.match(child):
			return None, None
		return parent, child

	def getXPathForElement(self, node):
		if node is None or node.nodeType == node.DOCUMENT_NODE:
			return ""
		count = 0
		sibling = node.previousSibling
		while sibling:
			if sibling.nodeType == sibling.ELEMENT_NODE and sibling.tagName == node.tagName:
				count += 1
			sibling = sibling.previousSibling
		countAfter = 0
		sibling = node.nextSibling
		while sibling:
			if sibling.nodeType == sibling.ELEMENT_NODE and sibling.tagName == node.tagName:
				countAfter += 1
			sibling = sibling.nextSibling
		if count+countAfter:
			index = '[%d]' % (count+1)
		else:
			index = ''
		return self.getXPathForElement(node.parentNode) + "/" + node.tagName + index

	def tagAndDictFromElement(self, item):
		t = item.tagName
		v = {}
		texts = []
		child = item.firstChild
		while child:
			if child.nodeType == child.ELEMENT_NODE:
				newt, newv = self.tagAndDictFromElement(child)
				# If the element already exists we turn it into a list (if not done before)
				if newt in v:
					if type(v[newt]) != type([]):
						v[newt] = [v[newt]]
					v[newt].append(newv)
				else:
					v[newt] = newv
			elif child.nodeType == child.ATTRIBUTE_NODE:
				v['@' + child.name] = child.value
			elif child.nodeType == child.TEXT_NODE:
				texts.append(child.data)
			child = child.nextSibling
		if len(texts) == 1:
			if v:
				v['#text'] = texts[0]
			else:
				v = texts[0]
				try:
					v = int(v)
				except ValueError:
					try:
						v = float(v)
					except ValueError:
						pass
				if v == 'null':
					v = None
				elif v == 'true':
					v = True
				elif v == 'false':
					v = False
		elif len(texts) > 1:
			v['#text'] = texts
		return t, v

	def elementFromTagAndData(self, tag, data):
		with self:
			newnode = self._doc.createElement(tag)
			if not isinstance(data, dict):
				# Not key/value, so a raw value. Convert to something string-like
				data = unicode(data)
				newnode.appendChild(self._doc.createTextNode(data))
				return newnode
			for k, v in data.items():
				if k == '#text':
					if not isinstance(v, list):
						v = [v]
					for string in v:
						newtextnode = self._doc.createTextNode(string)
						newnode.appendChild(newtextnode)
				elif k and k[0] == '@':
					attrname = k[1:]
					newattr = self._doc.createAttribute(attrname)
					newattr.value = v
					newnode.appendChild(newattr)
				else:
					if not isinstance(v, list):
						v = [v]
					for childdef in v:
						newchild = self.elementFromTagAndData(k, childdef)
						newnode.appendChild(newchild)
			return newnode
		
	def elementFromXML(self, xmltext):
		newdoc = xml.dom.minidom.parseString(xmltext)
		return newdoc.firstChild
		
	def setValue(self, location, value):
		"""Set (or insert) a single node by a given value (passed as a string)"""
		with self:
			# Find the node, if it exists.
			node = xpath.findnode(location, self._doc.documentElement)
			if node is None:
				# Does not exist yet. Try to add it.
				parent, child = self.splitXPath(location)
				if not parent or not child:
					raise DBKeyError("XPath %s does not refer to unique new or existing location" % location)
				return self.newValue(parent, 'child', child, value)

			# Sanity check
			if node.nodeType == node.DOCUMENT_NODE:
				raise DBParamError('Cannot replace value of /')
			
			# Clear out old contents of the node
			while node.firstChild: node.removeChild(node.firstChild)
		
			if hasattr(value, 'nodeType'):
				# It seems to be a DOM node. Insert it.
				node.appendChild(value)
			else:
				# Insert the new text value
				node.appendChild(self._doc.createTextNode(str(value)))
		
			# Signal anyone waiting
			self.signalNodelist([node])
		
			# Return the location of the new node
			return getXPathForElement(node)
		
	def newValue(self, location, where, name, value):
		"""Insert a single new node into the document (value passed as a string)"""
		with self:
			# Create the new node to be instered
			newnode = self._doc.createElement(name)
			newnode.appendChild(self._doc.createTextNode(str(value)))
		
			# Find the node that we want to insert it relative to
			node = xpath.findnode(location, self._doc.documentElement)
			if node is None:
				raise DBKeyError(location)
			
			# Insert it in the right place
			if where == 'before':
				node.parentNode.insertBefore(node, newnode)
			elif where == 'after':
				newnode.nextSibling = node.nextSibling
				node.nextSibling = newnode
			elif where == 'child':
				node.appendChild(newnode)
			else:
				raise DBParamError('where must be before, after or child')

			# Signal anyone waiting
			self.signalNodelist([newnode, newnode.parentNode])
		
			# Return the location of the new node
			return getXPathForElement(newnode)
		
	def delValue(self, location):
		"""Remove a single node from the document"""
		with self:
			node = xpath.findnode(location, self._doc.documentElement)
			if node is None:
				raise DBKeyError(location)
			parentNode = node.parentNode
			parentNode.removeChild(node)
		
			# Signal anyone waiting
			self.signalNodelist([parentNode])
		
	def delValues(self, location):
		"""Remove a (possibly empty) set of nodes from the document"""
		with self:
			nodelist = xpath.find(location, self._doc.documentElement)
			parentList = []
			print 'xxxjack delValues', repr(nodelist)
			for node in nodelist:
				parentNode = node.parentNode
				parentNode.removeChild(node)
				if not parentNode in parentList:
					parentList.append(parentNode)
			self.signalNodelist(parentList)
			
	def hasValue(self, location):
		"""Return xpath if the location exists, None otherwise"""
		with self:
			node = xpath.findnode(location, self._doc.documentElement)
			if node:
				return getXPathForElement(node)
			return None
		
	def waitValue(self, location):
		with self:
			node = xpath.findnode(location, self._doc.documentElement)
			if not node:
				self.waitLocation(location)
				node = xpath.findnode(location, self._doc.documentElement)
				assert node
			return getXPathForElement(node)

	def hasValues(self, location, context=None):
		"""Return a list of xpaths for the given location"""
		with self:
			if context is None:
				context = self._doc.documentElement
			nodelist = xpath.find(location, context)
			return map(getXPathForElement, nodelist)
		
	def getValue(self, location, context=None):
		"""Return a single value from the document (as string)"""
		with self:
			if context is None:
				context = self._doc.documentElement
			return xpath.findvalue(location, context)
		
	def getValues(self, location, context=None):
		"""Return a list of node values from the document (as names and strings)"""
		with self:
			if context is None:
				context = self._doc.documentElement
			nodelist = xpath.find(location, context)
			return self._getValueList(nodelist)
		
	def getElements(self, location, context=None):
		"""Return a list of DOM nodes (elements only, for now) that match the location"""
		with self:
			if context is None:
				context = self._doc.documentElement
			nodeList = xpath.find(location, context)
			return nodeList
		
	def _getValueList(self, nodelist):
		with self:
			rv = []
			for node in nodelist:
				rv.append((getXPathForElement(node), xpath.expr.string_value(node)))
			return rv
		
	def pullValue(self, location):
		"""Wait for a value, remove it from the document, return it (as string)"""
		with self:
			node = xpath.findnode(location, self._doc.documentElement)
			if not node:
				self.waitLocation(location)
				node = xpath.findnode(location, self._doc.documentElement)
				assert node
			rv = xpath.expr.string_value(node)
			node.parentNode.removeChild(node)
			self.signalNodelist([parentnode])
			return rv
  
	def pullValues(self, location):
		"""Wait for values, remove them from the document, return it (as list of strings)"""
		with self:
			nodelist = xpath.find(location, self._doc.documentElement)
			if not nodelist:
				self.waitLocation(location)
				nodelist = xpath.find(location, self._doc.documentElement)
			assert nodelist
			rv = self._getValueList(nodelist)
			parentList = []
			for node in nodelist:
				parentNode = node.parentNode
				parentNode.removeChild(node)
				if not parentNode in parentList:
					parentList.append(parentNode)
			self.signalNodelist(parentList)
			return rv
		
	def trackValue(self, location, generation):
		"""Generator. Like waitValue, but keeps on returning changed paths"""
		with self:
			generation = self.waitLocation(location, generation)
			node = xpath.findnode(location, self._doc.documentElement)
			assert node
			return getXPathForElement(node), generation