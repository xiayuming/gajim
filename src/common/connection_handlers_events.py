# -*- coding:utf-8 -*-
## src/common/connection_handlers_events.py
##
## Copyright (C) 2010 Yann Leboulanger <asterix AT lagaule.org>
##
## This file is part of Gajim.
##
## Gajim is free software; you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published
## by the Free Software Foundation; version 3 only.
##
## Gajim is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
## GNU General Public License for more details.
##
## You should have received a copy of the GNU General Public License
## along with Gajim. If not, see <http://www.gnu.org/licenses/>.
##

import datetime
import sys
from time import (localtime, time as time_time)
from calendar import timegm

from common import nec
from common import helpers
from common import gajim
from common import xmpp
from common import dataforms
from common import exceptions
from common.logger import LOG_DB_PATH

import logging
log = logging.getLogger('gajim.c.connection_handlers_events')

class HelperEvent:
    def get_jid_resource(self):
        if hasattr(self, 'id_') and self.id_ in self.conn.groupchat_jids:
            self.fjid = self.conn.groupchat_jids[self.id_]
            del self.conn.groupchat_jids[self.id_]
        else:
            self.fjid = helpers.get_full_jid_from_iq(self.iq_obj)
        self.jid, self.resource = gajim.get_room_and_nick_from_fjid(self.fjid)

    def get_id(self):
        self.id_ = self.iq_obj.getID()

class HttpAuthReceivedEvent(nec.NetworkIncomingEvent):
    name = 'http-auth-received'
    base_network_events = []

    def generate(self):
        self.opt = gajim.config.get_per('accounts', self.conn.name, 'http_auth')
        self.iq_id = self.iq_obj.getTagAttr('confirm', 'id')
        self.method = self.iq_obj.getTagAttr('confirm', 'method')
        self.url = self.iq_obj.getTagAttr('confirm', 'url')
        # In case it's a message with a body
        self.msg = self.iq_obj.getTagData('body')
        return True

class LastResultReceivedEvent(nec.NetworkIncomingEvent, HelperEvent):
    name = 'last-result-received'
    base_network_events = []

    def generate(self):
        self.get_id()
        self.get_jid_resource()
        if self.id_ in self.conn.last_ids:
            self.conn.last_ids.remove(self.id_)

        self.status = ''
        self.seconds = -1

        if self.iq_obj.getType() == 'error':
            return True

        qp = self.iq_obj.getTag('query')
        if not qp:
            return
        sec = qp.getAttr('seconds')
        self.status = qp.getData()
        try:
            self.seconds = int(sec)
        except Exception:
            return

        return True

class VersionResultReceivedEvent(nec.NetworkIncomingEvent, HelperEvent):
    name = 'version-result-received'
    base_network_events = []

    def generate(self):
        self.get_id()
        self.get_jid_resource()
        if self.id_ in self.conn.version_ids:
            self.conn.version_ids.remove(self.id_)

        self.client_info = ''
        self.os_info = ''

        if self.iq_obj.getType() == 'error':
            return True

        qp = self.iq_obj.getTag('query')
        if qp.getTag('name'):
            self.client_info += qp.getTag('name').getData()
        if qp.getTag('version'):
            self.client_info += ' ' + qp.getTag('version').getData()
        if qp.getTag('os'):
            self.os_info += qp.getTag('os').getData()

        return True

class TimeResultReceivedEvent(nec.NetworkIncomingEvent, HelperEvent):
    name = 'time-result-received'
    base_network_events = []

    def generate(self):
        self.get_id()
        self.get_jid_resource()
        if self.id_ in self.conn.entity_time_ids:
            self.conn.entity_time_ids.remove(self.id_)

        self.time_info = ''

        if self.iq_obj.getType() == 'error':
            return True

        qp = self.iq_obj.getTag('time')
        if not qp:
            # wrong answer
            return
        tzo = qp.getTag('tzo').getData()
        if tzo.lower() == 'z':
            tzo = '0:0'
        tzoh, tzom = tzo.split(':')
        utc_time = qp.getTag('utc').getData()
        ZERO = datetime.timedelta(0)
        class UTC(datetime.tzinfo):
            def utcoffset(self, dt):
                return ZERO
            def tzname(self, dt):
                return "UTC"
            def dst(self, dt):
                return ZERO

        class contact_tz(datetime.tzinfo):
            def utcoffset(self, dt):
                return datetime.timedelta(hours=int(tzoh), minutes=int(tzom))
            def tzname(self, dt):
                return "remote timezone"
            def dst(self, dt):
                return ZERO

        try:
            t = datetime.datetime.strptime(utc_time, '%Y-%m-%dT%H:%M:%SZ')
        except ValueError, e:
            try:
                t = datetime.datetime.strptime(utc_time,
                    '%Y-%m-%dT%H:%M:%S.%fZ')
            except ValueError, e:
                log.info('Wrong time format: %s' % str(e))
                return

        t = t.replace(tzinfo=UTC())
        self.time_info = t.astimezone(contact_tz()).strftime('%c')
        return True

class GMailQueryReceivedEvent(nec.NetworkIncomingEvent):
    name = 'gmail-notify'
    base_network_events = []

    def generate(self):
        if not self.iq_obj.getTag('mailbox'):
            return
        mb = self.iq_obj.getTag('mailbox')
        if not mb.getAttr('url'):
            return
        self.conn.gmail_url = mb.getAttr('url')
        if mb.getNamespace() != xmpp.NS_GMAILNOTIFY:
            return
        self.newmsgs = mb.getAttr('total-matched')
        if not self.newmsgs:
            return
        if self.newmsgs == '0':
            return
        # there are new messages
        self.gmail_messages_list = []
        if mb.getTag('mail-thread-info'):
            gmail_messages = mb.getTags('mail-thread-info')
            for gmessage in gmail_messages:
                unread_senders = []
                for sender in gmessage.getTag('senders').getTags(
                'sender'):
                    if sender.getAttr('unread') != '1':
                        continue
                    if sender.getAttr('name'):
                        unread_senders.append(sender.getAttr('name') + \
                            '< ' + sender.getAttr('address') + '>')
                    else:
                        unread_senders.append(sender.getAttr('address'))

                if not unread_senders:
                    continue
                gmail_subject = gmessage.getTag('subject').getData()
                gmail_snippet = gmessage.getTag('snippet').getData()
                tid = int(gmessage.getAttr('tid'))
                if not self.conn.gmail_last_tid or \
                tid > self.conn.gmail_last_tid:
                    self.conn.gmail_last_tid = tid
                self.gmail_messages_list.append({
                    'From': unread_senders,
                    'Subject': gmail_subject,
                    'Snippet': gmail_snippet,
                    'url': gmessage.getAttr('url'),
                    'participation': gmessage.getAttr('participation'),
                    'messages': gmessage.getAttr('messages'),
                    'date': gmessage.getAttr('date')})
            self.conn.gmail_last_time = int(mb.getAttr('result-time'))

        self.jid = gajim.get_jid_from_account(self.name)
        log.debug(('You have %s new gmail e-mails on %s.') % (self.newmsgs,
            self.jid))
        return True

class RosterItemExchangeEvent(nec.NetworkIncomingEvent, HelperEvent):
    name = 'roster-item-exchange-received'
    base_network_events = []

    def generate(self):
        self.get_id()
        self.get_jid_resource()
        self.exchange_items_list = {}
        items_list = self.iq_obj.getTag('x').getChildren()
        if not items_list:
            return
        self.action = items_list[0].getAttr('action')
        if self.action is None:
            self.action = 'add'
        for item in self.iq_obj.getTag('x', namespace=xmpp.NS_ROSTERX).\
        getChildren():
            try:
                jid = helpers.parse_jid(item.getAttr('jid'))
            except helpers.InvalidFormat:
                log.warn('Invalid JID: %s, ignoring it' % item.getAttr('jid'))
                continue
            name = item.getAttr('name')
            contact = gajim.contacts.get_contact(self.conn.name, jid)
            groups = []
            same_groups = True
            for group in item.getTags('group'):
                groups.append(group.getData())
                # check that all suggested groups are in the groups we have for
                # this contact
                if not contact or group not in contact.groups:
                    same_groups = False
            if contact:
                # check that all groups we have for this contact are in the
                # suggested groups
                for group in contact.groups:
                    if group not in groups:
                        same_groups = False
                if contact.sub in ('both', 'to') and same_groups:
                    continue
            self.exchange_items_list[jid] = []
            self.exchange_items_list[jid].append(name)
            self.exchange_items_list[jid].append(groups)
        if self.exchange_items_list:
            return True

class VersionRequestEvent(nec.NetworkIncomingEvent):
    name = 'version-request-received'
    base_network_events = []

class LastRequestEvent(nec.NetworkIncomingEvent):
    name = 'last-request-received'
    base_network_events = []

class TimeRequestEvent(nec.NetworkIncomingEvent):
    name = 'time-request-received'
    base_network_events = []

class TimeRevisedRequestEvent(nec.NetworkIncomingEvent):
    name = 'time-revised-request-received'
    base_network_events = []

class RosterReceivedEvent(nec.NetworkIncomingEvent):
    name = 'roster-received'
    base_network_events = []

    def generate(self):
        self.version = self.xmpp_roster.version
        self.received_from_server = self.xmpp_roster.received_from_server
        self.roster = {}
        raw_roster = self.xmpp_roster.getRaw()
        our_jid = gajim.get_jid_from_account(self.name)

        for jid in raw_roster:
            try:
                j = helpers.parse_jid(jid)
            except Exception:
                print >> sys.stderr, _('JID %s is not RFC compliant. It will not be added to your roster. Use roster management tools such as http://jru.jabberstudio.org/ to remove it') % jid
            else:
                infos = raw_roster[jid]
                if jid != our_jid and (not infos['subscription'] or \
                infos['subscription'] == 'none') and (not infos['ask'] or \
                infos['ask'] == 'none') and not infos['name'] and \
                not infos['groups']:
                    # remove this useless item, it won't be shown in roster anyway
                    self.conn.connection.getRoster().delItem(jid)
                elif jid != our_jid: # don't add our jid
                    self.roster[j] = raw_roster[jid]
        return True

class RosterSetReceivedEvent(nec.NetworkIncomingEvent):
    name = 'roster-set-received'
    base_network_events = []

    def generate(self):
        self.version = self.iq_obj.getTagAttr('query', 'ver')
        self.items = {}
        for item in self.iq_obj.getTag('query').getChildren():
            try:
                jid = helpers.parse_jid(item.getAttr('jid'))
            except helpers.InvalidFormat:
                log.warn('Invalid JID: %s, ignoring it' % item.getAttr('jid'))
                continue
            name = item.getAttr('name')
            sub = item.getAttr('subscription')
            ask = item.getAttr('ask')
            groups = []
            for group in item.getTags('group'):
                groups.append(group.getData())
            self.items[jid] = {'name': name, 'sub': sub, 'ask': ask,
                'groups': groups}
        if self.conn.connection and self.conn.connected > 1:
            reply = xmpp.Iq(typ='result', attrs={'id': self.iq_obj.getID()},
                to=self.iq_obj.getFrom(), frm=self.iq_obj.getTo(), xmlns=None)
            self.conn.connection.send(reply)
        return True

class RosterInfoEvent(nec.NetworkIncomingEvent):
    name = 'roster-info'
    base_network_events = []

class MucOwnerReceivedEvent(nec.NetworkIncomingEvent, HelperEvent):
    name = 'muc-owner-received'
    base_network_events = []

    def generate(self):
        self.get_jid_resource()
        qp = self.iq_obj.getQueryPayload()
        self.form_node = None
        for q in qp:
            if q.getNamespace() == xmpp.NS_DATA:
                self.form_node = q
                self.dataform = dataforms.ExtendForm(node=self.form_node)
                return True

class MucAdminReceivedEvent(nec.NetworkIncomingEvent, HelperEvent):
    name = 'muc-admin-received'
    base_network_events = []

    def generate(self):
        self.get_jid_resource()
        items = self.iq_obj.getTag('query',
            namespace=xmpp.NS_MUC_ADMIN).getTags('item')
        self.users_dict = {}
        for item in items:
            if item.has_attr('jid') and item.has_attr('affiliation'):
                try:
                    jid = helpers.parse_jid(item.getAttr('jid'))
                except helpers.InvalidFormat:
                    log.warn('Invalid JID: %s, ignoring it' % \
                        item.getAttr('jid'))
                    continue
                affiliation = item.getAttr('affiliation')
                self.users_dict[jid] = {'affiliation': affiliation}
                if item.has_attr('nick'):
                    self.users_dict[jid]['nick'] = item.getAttr('nick')
                if item.has_attr('role'):
                    self.users_dict[jid]['role'] = item.getAttr('role')
                reason = item.getTagData('reason')
                if reason:
                    self.users_dict[jid]['reason'] = reason
        return True

class PrivateStorageReceivedEvent(nec.NetworkIncomingEvent):
    name = 'private-storage-received'
    base_network_events = []

    def generate(self):
        query = self.iq_obj.getTag('query')
        self.storage_node = query.getTag('storage')
        if self.storage_node:
            self.namespace = self.storage_node.getNamespace()
            return True

class BookmarksHelper:
    def parse_bookmarks(self):
        self.bookmarks = []
        confs = self.base_event.storage_node.getTags('conference')
        for conf in confs:
            autojoin_val = conf.getAttr('autojoin')
            if autojoin_val is None: # not there (it's optional)
                autojoin_val = False
            minimize_val = conf.getAttr('minimize')
            if minimize_val is None: # not there (it's optional)
                minimize_val = False
            print_status = conf.getTagData('print_status')
            if not print_status:
                print_status = conf.getTagData('show_status')
            try:
                jid = helpers.parse_jid(conf.getAttr('jid'))
            except helpers.InvalidFormat:
                log.warn('Invalid JID: %s, ignoring it' % conf.getAttr('jid'))
                continue
            bm = {'name': conf.getAttr('name'),
                'jid': jid,
                'autojoin': autojoin_val,
                'minimize': minimize_val,
                'password': conf.getTagData('password'),
                'nick': conf.getTagData('nick'),
                'print_status': print_status}


            bm_jids = [b['jid'] for b in self.bookmarks]
            if bm['jid'] not in bm_jids:
                self.bookmarks.append(bm)

class PrivateStorageBookmarksReceivedEvent(nec.NetworkIncomingEvent,
BookmarksHelper):
    name = 'private-storage-bookmarks-received'
    base_network_events = ['private-storage-received']

    def generate(self):
        self.conn = self.base_event.conn
        if self.base_event.namespace != 'storage:bookmarks':
            return
        self.parse_bookmarks()
        return True

class BookmarksReceivedEvent(nec.NetworkIncomingEvent):
    name = 'bookmarks-received'
    base_network_events = ['private-storage-bookmarks-received',
        'pubsub-bookmarks-received']

    def generate(self):
        self.conn = self.base_event.conn
        self.bookmarks = self.base_event.bookmarks
        return True

class PrivateStorageRosternotesReceivedEvent(nec.NetworkIncomingEvent):
    name = 'private-storage-rosternotes-received'
    base_network_events = ['private-storage-received']

    def generate(self):
        self.conn = self.base_event.conn
        if self.base_event.namespace != 'storage:rosternotes':
            return
        notes = self.base_event.storage_node.getTags('note')
        self.annotations = {}
        for note in notes:
            try:
                jid = helpers.parse_jid(note.getAttr('jid'))
            except helpers.InvalidFormat:
                log.warn('Invalid JID: %s, ignoring it' % note.getAttr('jid'))
                continue
            annotation = note.getData()
            self.annotations[jid] = annotation
        if self.annotations:
            return True

class RosternotesReceivedEvent(nec.NetworkIncomingEvent):
    name = 'rosternotes-received'
    base_network_events = ['private-storage-rosternotes-received']

    def generate(self):
        self.conn = self.base_event.conn
        self.annotations = self.base_event.annotations
        return True

class PubsubReceivedEvent(nec.NetworkIncomingEvent):
    name = 'pubsub-received'
    base_network_events = []

    def generate(self):
        self.pubsub_node = self.iq_obj.getTag('pubsub')
        if not self.pubsub_node:
            return
        self.items_node = self.pubsub_node.getTag('items')
        if not self.items_node:
            return
        self.item_node = self.items_node.getTag('item')
        if not self.item_node:
            return
        return True

class PubsubBookmarksReceivedEvent(nec.NetworkIncomingEvent, BookmarksHelper):
    name = 'pubsub-bookmarks-received'
    base_network_events = ['pubsub-received']

    def generate(self):
        self.conn = self.base_event.conn
        storage = self.base_event.item_node.getTag('storage')
        if not storage:
            return
        ns = storage.getNamespace()
        if ns != 'storage:bookmarks':
            return
        self.parse_bookmarks()
        return True

class SearchFormReceivedEvent(nec.NetworkIncomingEvent, HelperEvent):
    name = 'search-form-received'
    base_network_events = []

    def generate(self):
        self.get_jid_resource()
        self.data = None
        self.is_dataform = False
        tag = self.iq_obj.getTag('query', namespace=xmpp.NS_SEARCH)
        if not tag:
            return True
        self.data = tag.getTag('x', namespace=xmpp.NS_DATA)
        if self.data:
            self.is_dataform = True
            return True
        self.data = {}
        for i in self.iq_obj.getQueryPayload():
            self.data[i.getName()] = i.getData()
        return True


class SearchResultReceivedEvent(nec.NetworkIncomingEvent, HelperEvent):
    name = 'search-result-received'
    base_network_events = []

    def generate(self):
        self.get_jid_resource()
        self.data = None
        self.is_dataform = False
        tag = self.iq_obj.getTag('query', namespace=xmpp.NS_SEARCH)
        if not tag:
            return True
        self.data = tag.getTag('x', namespace=xmpp.NS_DATA)
        if self.data:
            self.is_dataform = True
            return True
        self.data = []
        for item in tag.getTags('item'):
            # We also show attributes. jid is there
            f = item.attrs
            for i in item.getPayload():
                f[i.getName()] = i.getData()
            self.data.append(f)
        return True

class ErrorReceivedEvent(nec.NetworkIncomingEvent, HelperEvent):
    name = 'error-received'
    base_network_events = []

    def generate(self):
        self.get_id()
        self.get_jid_resource()
        self.errmsg = self.iq_obj.getErrorMsg()
        self.errcode = self.iq_obj.getErrorCode()
        return True

class GmailNewMailReceivedEvent(nec.NetworkIncomingEvent):
    name = 'gmail-new-mail-received'
    base_network_events = []

    def generate(self):
        if not self.iq_obj.getTag('new-mail'):
            return
        if self.iq_obj.getTag('new-mail').getNamespace() != xmpp.NS_GMAILNOTIFY:
            return
        return True

class PingReceivedEvent(nec.NetworkIncomingEvent):
    name = 'ping-received'
    base_network_events = []

class StreamReceivedEvent(nec.NetworkIncomingEvent):
    name = 'stream-received'
    base_network_events = []

class StreamConflictReceivedEvent(nec.NetworkIncomingEvent):
    name = 'stream-conflict-received'
    base_network_events = ['stream-received']

    def generate(self):
        if self.base_event.iq_obj.getTag('conflict'):
            self.conn = self.base_event.conn
            return True

class PresenceReceivedEvent(nec.NetworkIncomingEvent, HelperEvent):
    name = 'presence-received'
    base_network_events = ['raw-pres-received']

    def generate(self):
        self.conn = self.base_event.conn
        self.iq_obj = self.base_event.iq_obj

        self.need_add_in_roster = False
        self.need_redraw = False

        self.ptype = self.iq_obj.getType()
        if self.ptype == 'available':
            self.ptype = None
        rfc_types = ('unavailable', 'error', 'subscribe', 'subscribed',
            'unsubscribe', 'unsubscribed')
        if self.ptype and not self.ptype in rfc_types:
            self.ptype = None
        if not self.conn or self.conn.connected < 2:
            log.debug('account is no more connected')
            return
        try:
            self.get_jid_resource()
        except Exception:
            if self.iq_obj.getTag('error') and self.iq_obj.getTag('error').\
            getTag('jid-malformed'):
                # wrong jid, we probably tried to change our nick in a room to a non
                # valid one
                who = str(self.iq_obj.getFrom())
                jid_stripped, resource = gajim.get_room_and_nick_from_fjid(who)
                self.conn.dispatch('GC_MSG', (jid_stripped,
                    _('Nickname not allowed: %s') % resource, None, False, None,
                    []))
            return
        jid_list = gajim.contacts.get_jid_list(self.conn.name)
        self.timestamp = None
        self.get_id()
        self.is_gc = False # is it a GC presence ?
        sigTag = None
        ns_muc_user_x = None
        avatar_sha = None
        # XEP-0172 User Nickname
        self.user_nick = self.iq_obj.getTagData('nick') or ''
        self.contact_nickname = None
        transport_auto_auth = False
        # XEP-0203
        delay_tag = self.iq_obj.getTag('delay', namespace=xmpp.NS_DELAY2)
        if delay_tag:
            tim = self.iq_obj.getTimestamp2()
            tim = helpers.datetime_tuple(tim)
            self.timestamp = localtime(timegm(tim))
        xtags = self.iq_obj.getTags('x')
        for x in xtags:
            namespace = x.getNamespace()
            if namespace.startswith(xmpp.NS_MUC):
                self.is_gc = True
                if namespace == xmpp.NS_MUC_USER and x.getTag('destroy'):
                    ns_muc_user_x = x
            elif namespace == xmpp.NS_SIGNED:
                sigTag = x
            elif namespace == xmpp.NS_VCARD_UPDATE:
                avatar_sha = x.getTagData('photo')
                self.contact_nickname = x.getTagData('nickname')
            elif namespace == xmpp.NS_DELAY and not self.timestamp:
                # XEP-0091
                tim = self.iq_obj.getTimestamp()
                tim = helpers.datetime_tuple(tim)
                self.timestamp = localtime(timegm(tim))
            elif namespace == 'http://delx.cjb.net/protocol/roster-subsync':
                # see http://trac.gajim.org/ticket/326
                agent = gajim.get_server_from_jid(self.jid)
                if self.conn.connection.getRoster().getItem(agent):
                    # to be sure it's a transport contact
                    transport_auto_auth = True

        if not self.is_gc and self.id_ and self.id_.startswith('gajim_muc_') \
        and self.ptype == 'error':
            # Error presences may not include sent stanza, so we don't detect it's
            # a muc preence. So detect it by ID
            h = hmac.new(self.conn.secret_hmac, self.jid).hexdigest()[:6]
            if self.id_.split('_')[-1] == h:
                self.is_gc = True
        self.status = self.iq_obj.getStatus() or ''
        self.show = self.iq_obj.getShow()
        if self.show not in ('chat', 'away', 'xa', 'dnd'):
            self.show = '' # We ignore unknown show
        if not self.ptype and not self.show:
            self.show = 'online'
        elif self.ptype == 'unavailable':
            self.show = 'offline'

        self.prio = self.iq_obj.getPriority()
        try:
            self.prio = int(self.prio)
        except Exception:
            self.prio = 0
        self.keyID = ''
        if sigTag and self.conn.USE_GPG and self.ptype != 'error':
            # error presences contain our own signature
            # verify
            sigmsg = sigTag.getData()
            self.keyID = self.conn.gpg.verify(self.status, sigmsg)
            self.keyID = helpers.prepare_and_validate_gpg_keyID(self.conn.name,
                self.jid, self.keyID)

        if self.is_gc:
            if self.ptype == 'error':
                errcon = self.iq_obj.getError()
                errmsg = self.iq_obj.getErrorMsg()
                errcode = self.iq_obj.getErrorCode()

                gc_control = gajim.interface.msg_win_mgr.get_gc_control(
                    self.jid, self.conn.name)

                # If gc_control is missing - it may be minimized. Try to get it
                # from there. If it's not there - then it's missing anyway and
                # will remain set to None.
                if gc_control is None:
                    minimized = gajim.interface.minimized_controls[
                        self.conn.name]
                    gc_control = minimized.get(self.jid)

                if errcode == '502':
                    # Internal Timeout:
                    self.show = 'error'
                    self.status = errmsg
                    return True
                elif errcode == '503':
                    if gc_control is None or gc_control.autorejoin is None:
                        # maximum user number reached
                        self.conn.dispatch('GC_ERROR', (gc_control,
                            _('Unable to join group chat'),
                            _('Maximum number of users for %s has been '
                            'reached') % self.jid))
                elif (errcode == '401') or (errcon == 'not-authorized'):
                    # password required to join
                    self.conn.dispatch('GC_PASSWORD_REQUIRED', (self.jid,
                        self.resource))
                elif (errcode == '403') or (errcon == 'forbidden'):
                    # we are banned
                    self.conn.dispatch('GC_ERROR', (gc_control,
                        _('Unable to join group chat'),
                        _('You are banned from group chat %s.') % self.jid))
                elif (errcode == '404') or (errcon in ('item-not-found',
                'remote-server-not-found')):
                    if gc_control is None or gc_control.autorejoin is None:
                        # group chat does not exist
                        self.conn.dispatch('GC_ERROR', (gc_control,
                            _('Unable to join group chat'),
                            _('Group chat %s does not exist.') % self.jid))
                elif (errcode == '405') or (errcon == 'not-allowed'):
                    self.conn.dispatch('GC_ERROR', (gc_control,
                        _('Unable to join group chat'),
                        _('Group chat creation is restricted.')))
                elif (errcode == '406') or (errcon == 'not-acceptable'):
                    self.conn.dispatch('GC_ERROR', (gc_control,
                        _('Unable to join group chat'),
                        _('Your registered nickname must be used in group chat '
                        '%s.') % self.jid))
                elif (errcode == '407') or (errcon == 'registration-required'):
                    self.conn.dispatch('GC_ERROR', (gc_control,
                        _('Unable to join group chat'),
                        _('You are not in the members list in groupchat %s.') %\
                        self.jid))
                elif (errcode == '409') or (errcon == 'conflict'):
                    # nick conflict
                    self.conn.dispatch('ASK_NEW_NICK', (self.jid,))
                else:   # print in the window the error
                    self.conn.dispatch('ERROR_ANSWER', ('', self.jid,
                            errmsg, errcode))
            elif not self.ptype or self.ptype == 'unavailable':
                if gajim.config.get('log_contact_status_changes') and \
                gajim.config.should_log(self.conn.name, self.jid):
                    gc_c = gajim.contacts.get_gc_contact(self.conn.name,
                        self.jid, self.resource)
                    st = self.status or ''
                    if gc_c:
                        jid = gc_c.jid
                    else:
                        jid = self.iq_obj.getJid()
                    if jid:
                        # we know real jid, save it in db
                        st += ' (%s)' % jid
                    try:
                        gajim.logger.write('gcstatus', self.fjid, st, self.show)
                    except exceptions.PysqliteOperationalError, e:
                        self.conn.dispatch('DB_ERROR', (_('Disk Write Error'),
                            str(e)))
                    except exceptions.DatabaseMalformed:
                        pritext = _('Database Error')
                        sectext = _('The database file (%s) cannot be read. '
                            'Try to repair it (see '
                            'http://trac.gajim.org/wiki/DatabaseBackup) or '
                            'remove it (all history will be lost).') % \
                            LOG_DB_PATH
                        self.conn.dispatch('DB_ERROR', (pritext, sectext))
                if avatar_sha == '':
                    # contact has no avatar
                    puny_nick = helpers.sanitize_filename(self.resource)
                    gajim.interface.remove_avatar_files(self.jid, puny_nick)
                # NOTE: if it's a gc presence, don't ask vcard here.
                # We may ask it to real jid in gui part.
                if ns_muc_user_x:
                    # Room has been destroyed. see
                    # http://www.xmpp.org/extensions/xep-0045.html#destroyroom
                    reason = _('Room has been destroyed')
                    destroy = ns_muc_user_x.getTag('destroy')
                    r = destroy.getTagData('reason')
                    if r:
                        reason += ' (%s)' % r
                    if destroy.getAttr('jid'):
                        try:
                            jid = helpers.parse_jid(destroy.getAttr('jid'))
                            reason += '\n' + \
                                _('You can join this room instead: %s') % jid
                        except common.helpers.InvalidFormat:
                            pass
                    statusCode = ['destroyed']
                else:
                    reason = self.iq_obj.getReason()
                    statusCode = self.iq_obj.getStatusCode()
                role = self.iq_obj.getRole()
                affiliation = self.iq_obj.getAffiliation()
                prs_jid = self.iq_obj.getJid()
                actor = self.iq_obj.getActor()
                new_nick = self.iq_obj.getNewNick()
                self.conn.dispatch('GC_NOTIFY', (self.jid, self.show,
                    self.status, self.resource, role, affiliation, prs_jid,
                    reason, actor, statusCode, new_nick, avatar_sha))
            return

        if self.ptype == 'subscribe':
            log.debug('subscribe request from %s' % self.jfid)
            if self.fjid.find('@') <= 0 and self.fjid in \
            self.agent_registrations:
                self.agent_registrations[self.fjid]['sub_received'] = True
                if not self.agent_registrations[self.fjid]['roster_push']:
                    # We'll reply after roster push result
                    return
            if gajim.config.get_per('accounts', self.conn.name, 'autoauth') or \
            self.fjid.find('@') <= 0 or self.jid in self.jids_for_auto_auth or \
            transport_auto_auth:
                if self.conn.connection:
                    p = xmpp.Presence(self.fjid, 'subscribed')
                    p = self.conn.add_sha(p)
                    self.conn.connection.send(p)
                if self.fjid.find('@') <= 0 or transport_auto_auth:
                    self.show = 'offline'
                    self.status = 'offline'
                    return True

                if transport_auto_auth:
                    self.conn.automatically_added.append(self.jid)
                    self.conn.request_subscription(self.jid,
                        name=self.user_nick)
            else:
                if not self.status:
                    self.status = _('I would like to add you to my roster.')
                self.conn.dispatch('SUBSCRIBE', (self.jid, self.status,
                    self.user_nick))
        elif self.ptype == 'subscribed':
            if self.jid in self.conn.automatically_added:
                self.conn.automatically_added.remove(self.jid)
            else:
                # detect a subscription loop
                if self.jid not in self.conn.subscribed_events:
                    self.conn.subscribed_events[self.jid] = []
                self.conn.subscribed_events[self.jid].append(time_time())
                block = False
                if len(self.conn.subscribed_events[self.jid]) > 5:
                    if time_time() - self.subscribed_events[self.jid][0] < 5:
                        block = True
                    self.conn.subscribed_events[self.jid] = \
                        self.conn.subscribed_events[self.jid][1:]
                if block:
                    gajim.config.set_per('account', self.conn.name,
                        'dont_ack_subscription', True)
                else:
                    self.conn.dispatch('SUBSCRIBED', (self.jid, self.resource))
            # BE CAREFUL: no con.updateRosterItem() in a callback
            log.debug(_('we are now subscribed to %s') % self.jid)
        elif self.ptype == 'unsubscribe':
            log.debug(_('unsubscribe request from %s') % self.jid)
        elif self.ptype == 'unsubscribed':
            log.debug(_('we are now unsubscribed from %s') % self.jid)
            # detect a unsubscription loop
            if self.jid not in self.conn.subscribed_events:
                self.conn.subscribed_events[self.jid] = []
            self.conn.subscribed_events[self.jid].append(time_time())
            block = False
            if len(self.conn.subscribed_events[self.jid]) > 5:
                if time_time() - self.conn.subscribed_events[self.jid][0] < 5:
                    block = True
                self.conn.subscribed_events[self.jid] = \
                    self.conn.subscribed_events[self.jid][1:]
            if block:
                gajim.config.set_per('account', self.conn.name,
                    'dont_ack_subscription', True)
            else:
                self.dispatch('UNSUBSCRIBED', self.jid)
        elif self.ptype == 'error':
            errmsg = self.iq_obj.getError()
            errcode = self.iq_obj.getErrorCode()
            if errcode != '502': # Internal Timeout:
                # print in the window the error
                self.conn.dispatch('ERROR_ANSWER', ('', self.jid, errmsg, errcode))
            if errcode != '409': # conflict # See #5120
                self.show = 'error'
                self.status = errmsg
                return True

        elif self.ptype == 'unavailable':
            for jid in [self.jid, self.fjid]:
                if jid not in self.conn.sessions:
                    continue
                # automatically terminate sessions that they haven't sent a thread
                # ID in, only if other part support thread ID
                for sess in self.conn.sessions[jid].values():
                    if not sess.received_thread_id:
                        contact = gajim.contacts.get_contact(self.conn.name,
                            jid)
                        # FIXME: I don't know if this is the correct behavior here.
                        # Anyway, it is the old behavior when we assumed that
                        # not-existing contacts don't support anything
                        contact_exists = bool(contact)
                        session_supported = contact_exists and (
                            contact.supports(xmpp.NS_SSN) or
                            contact.supports(xmpp.NS_ESESSION))
                        if session_supported:
                            sess.terminate()
                            del self.conn.sessions[jid][sess.thread_id]

        if avatar_sha is not None and self.ptype != 'error':
            if self.jid not in self.conn.vcard_shas:
                cached_vcard = self.conn.get_cached_vcard(self.jid)
                if cached_vcard and 'PHOTO' in cached_vcard and \
                'SHA' in cached_vcard['PHOTO']:
                    self.conn.vcard_shas[self.jid] = \
                        cached_vcard['PHOTO']['SHA']
                else:
                    self.conn.vcard_shas[self.jid] = ''
            if avatar_sha != self.conn.vcard_shas[self.jid]:
                # avatar has been updated
                self.conn.request_vcard(self.jid)

        if not self.ptype or self.ptype == 'unavailable':
            if gajim.config.get('log_contact_status_changes') and \
            gajim.config.should_log(self.conn.name, self.jid):
                try:
                    gajim.logger.write('status', self.jid, self.status,
                        self.show)
                except exceptions.PysqliteOperationalError, e:
                    self.conn.dispatch('DB_ERROR', (_('Disk Write Error'), str(e)))
                except exceptions.DatabaseMalformed:
                    pritext = _('Database Error')
                    sectext = _('The database file (%s) cannot be read. Try to '
                        'repair it (see '
                        'http://trac.gajim.org/wiki/DatabaseBackup) or remove '
                        'it (all history will be lost).') % LOG_DB_PATH
                    self.conn.dispatch('DB_ERROR', (pritext, sectext))
            our_jid = gajim.get_jid_from_account(self.conn.name)
            if self.jid == our_jid and self.resource == \
            self.conn.server_resource:
                # We got our own presence
                self.conn.dispatch('STATUS', self.show)
            elif self.jid in jid_list:
                return True