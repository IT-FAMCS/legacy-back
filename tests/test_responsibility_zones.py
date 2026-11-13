import unittest
import test_telegram as identity
import models
import database

class ZoneTests(unittest.TestCase):
    setUp=identity.TelegramTests.setUp;tearDown=identity.TelegramTests.tearDown
    tearDownClass=classmethod(lambda cls:database.engine.dispose())
    web=identity.TelegramTests.web;bot=identity.TelegramTests.bot;issue=identity.TelegramTests.issue;bind=identity.TelegramTests.bind

    def path(self,card=None):return f"/api/cards/{card or self.cards['Посвящение']}/responsibility-zones"
    def create(self,**values):
        board=self.web('GET',self.path())[1]
        status,result=self.web('POST',self.path(),{'title':'Сцена','people_required':2,'board_version':board['version'],**values})
        self.assertEqual(status,201,result);return result

    def test_event_only_scope_and_edit_permissions(self):
        self.assertEqual(self.web('GET',self.path(self.cards['IT']))[0],422)
        board=self.create()
        uid=self.users['Другой участник'];self.db.get(models.User,uid).position_ref.can_edit_cards=False;self.db.commit()
        self.assertEqual(self.web('GET',self.path(),role='Другой участник')[0],200)
        self.assertEqual(self.web('POST',self.path(),{'title':'Нельзя','people_required':1,'board_version':board['version']},role='Другой участник')[0],403)
        self.assertEqual(self.web('POST',self.path()+'/clear',{'board_version':board['version']},role='Другой участник')[0],403)
        self.db.get(models.Card,self.cards['Посвящение']).access_logins='nobody';self.db.commit()
        self.assertEqual(self.web('GET',self.path())[0],404)

    def test_staffing_counts_include_lead_and_show_telegram_contacts(self):
        self.db.get(models.User,self.users['Участник']).telegram='@anna';self.db.commit()
        board=self.create(lead_user_id=self.users['Участник'])
        zone=board['zones'][0];self.assertEqual(board['closed'],0);self.assertEqual(zone['filled'],1)
        self.assertEqual(zone['lead']['telegram'],'@anna')
        status,board=self.web('PATCH',self.path()+f"/{zone['id']}",{'version':zone['version'],'board_version':board['version'],'helper_ids':[self.users['Другой участник']]})
        self.assertEqual(status,200,board);self.assertEqual(board['closed'],1);self.assertEqual(board['open'],0)
        self.db.get(models.User,self.users['Участник']).is_deactivated=True;self.db.commit()
        board=self.web('GET',self.path(),role='Другой участник')[1];self.assertEqual(board['closed'],0);self.assertEqual(board['zones'][0]['filled'],1)

    def test_duplicate_lead_helper_and_invalid_headcount_are_rejected(self):
        for values in [{'people_required':0},{'lead_user_id':self.users['Участник'],'helper_ids':[self.users['Участник']]},{'helper_ids':[9999]}]:
            self.assertEqual(self.web('POST',self.path(),{'title':'Сцена','people_required':2,'board_version':0,**values})[0],422)

    def test_clear_is_atomic_versioned_preserves_zones_and_history(self):
        board=self.create(lead_user_id=self.users['Участник'],helper_ids=[self.users['Другой участник']])
        board=self.create(title='Звук',lead_user_id=self.users['Другой участник'],people_required=1)
        before=board['version']
        self.assertEqual(self.web('POST',self.path()+'/clear',{'board_version':before-1})[0],409)
        status,board=self.web('POST',self.path()+'/clear',{'board_version':before})
        self.assertEqual(status,200,board);self.assertEqual(len(board['zones']),2);self.assertEqual(board['closed'],0)
        self.assertTrue(all(z['lead'] is None and z['helper_ids']==[] for z in board['zones']))
        history=self.web('GET',self.path()+'/history')[1]
        self.assertEqual(len([e for e in history if e['action']=='clear']),2)
        self.assertTrue(history[0]['before']['lead'])

    def test_bot_zones_use_same_permissions_replay_and_lifecycle_guards(self):
        self.bind()
        status,events=self.bot('GET','bot/events');self.assertEqual(status,200);self.assertIn(self.cards['Посвящение'],[e['id'] for e in events])
        route=f"bot/events/{self.cards['Посвящение']}/responsibility-zones"
        status,board=self.bot('POST',route,{'title':'Встреча','people_required':1,'board_version':0},request_id='zone-create')
        self.assertEqual(status,201,board)
        self.assertEqual(self.bot('POST',route,{'title':'Встреча','people_required':1,'board_version':0},request_id='zone-create')[1],board)
        self.assertEqual(self.web('DELETE',f"/api/cards/{self.cards['Посвящение']}")[0],409)
        self.db.get(models.User,self.users['Участник']).position_ref.can_edit_cards=False;self.db.commit()
        self.assertEqual(self.bot('POST',route+'/clear',{'board_version':board['version']},request_id='clear')[0],403)

    def test_deleting_empty_event_drops_its_zone_board_and_private_history(self):
        category=self.db.get(models.Card,self.cards['Посвящение']).category_id
        status,card=self.web('POST','/api/cards',{'title':'Временное мероприятие','category_id':category})
        self.assertEqual(status,201)
        path=self.path(card['id'])
        status,board=self.web('POST',path,{'title':'Закрытая зона','people_required':1,'board_version':0})
        self.assertEqual(status,201)
        zone=board['zones'][0]
        self.assertEqual(self.web('DELETE',path+f"/{zone['id']}",{'version':zone['version'],'board_version':board['version']})[0],200)
        self.assertEqual(self.web('DELETE',f"/api/cards/{card['id']}")[0],204)
        self.db.expire_all()
        self.assertIsNone(self.db.get(models.ResponsibilityBoard,card['id']))
        self.assertEqual(self.db.query(models.ResponsibilityHistory).filter_by(card_id=card['id']).count(),0)
