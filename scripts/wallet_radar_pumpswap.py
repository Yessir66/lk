"""Read-only PumpSwap event/pool ABI, @pump-fun/pump-swap-sdk 2.2.0. No signer or transport."""
import base64
import struct
PROGRAM='pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA'
BUY_DISC=bytes([103,244,82,31,44,245,119,119])
SELL_DISC=bytes([62,47,55,10,165,3,220,42])
POOL_DISC=bytes([241,154,109,4,17,177,109,188])

BUY_FIELDS=[('timestamp', 'i64'),
 ('base_amount_out', 'u64'),
 ('max_quote_amount_in', 'u64'),
 ('user_base_token_reserves', 'u64'),
 ('user_quote_token_reserves', 'u64'),
 ('pool_base_token_reserves', 'u64'),
 ('pool_quote_token_reserves', 'u64'),
 ('quote_amount_in', 'u64'),
 ('lp_fee_basis_points', 'u64'),
 ('lp_fee', 'u64'),
 ('protocol_fee_basis_points', 'u64'),
 ('protocol_fee', 'u64'),
 ('quote_amount_in_with_lp_fee', 'u64'),
 ('user_quote_amount_in', 'u64'),
 ('pool', 'pubkey'),
 ('user', 'pubkey'),
 ('user_base_token_account', 'pubkey'),
 ('user_quote_token_account', 'pubkey'),
 ('protocol_fee_recipient', 'pubkey'),
 ('protocol_fee_recipient_token_account', 'pubkey'),
 ('coin_creator', 'pubkey'),
 ('coin_creator_fee_basis_points', 'u64'),
 ('coin_creator_fee', 'u64'),
 ('track_volume', 'bool'),
 ('total_unclaimed_tokens', 'u64'),
 ('total_claimed_tokens', 'u64'),
 ('current_sol_volume', 'u64'),
 ('last_update_timestamp', 'i64'),
 ('min_base_amount_out', 'u64'),
 ('ix_name', 'string'),
 ('cashback_fee_basis_points', 'u64'),
 ('cashback', 'u64'),
 ('buyback_fee_basis_points', 'u64'),
 ('buyback_fee', 'u64'),
 ('virtual_quote_reserves', 'i128'),
 ('can_boost', 'bool'),
 ('base_supply', 'u64'),
 ('holder_rewards_bps', 'u64'),
 ('holder_rewards', 'u64'),
 ('creator_fee_unclaimed', 'u64'),
 ('quote_transfer_fee', 'u64')]
SELL_FIELDS=[('timestamp', 'i64'),
 ('base_amount_in', 'u64'),
 ('min_quote_amount_out', 'u64'),
 ('user_base_token_reserves', 'u64'),
 ('user_quote_token_reserves', 'u64'),
 ('pool_base_token_reserves', 'u64'),
 ('pool_quote_token_reserves', 'u64'),
 ('quote_amount_out', 'u64'),
 ('lp_fee_basis_points', 'u64'),
 ('lp_fee', 'u64'),
 ('protocol_fee_basis_points', 'u64'),
 ('protocol_fee', 'u64'),
 ('quote_amount_out_without_lp_fee', 'u64'),
 ('user_quote_amount_out', 'u64'),
 ('pool', 'pubkey'),
 ('user', 'pubkey'),
 ('user_base_token_account', 'pubkey'),
 ('user_quote_token_account', 'pubkey'),
 ('protocol_fee_recipient', 'pubkey'),
 ('protocol_fee_recipient_token_account', 'pubkey'),
 ('coin_creator', 'pubkey'),
 ('coin_creator_fee_basis_points', 'u64'),
 ('coin_creator_fee', 'u64'),
 ('cashback_fee_basis_points', 'u64'),
 ('cashback', 'u64'),
 ('buyback_fee_basis_points', 'u64'),
 ('buyback_fee', 'u64'),
 ('virtual_quote_reserves', 'i128'),
 ('can_boost', 'bool'),
 ('base_supply', 'u64'),
 ('holder_rewards_bps', 'u64'),
 ('holder_rewards', 'u64'),
 ('creator_fee_unclaimed', 'u64'),
 ('quote_transfer_fee', 'u64')]
POOL_FIELDS=[('pool_bump', 'u8'),
 ('index', 'u16'),
 ('creator', 'pubkey'),
 ('base_mint', 'pubkey'),
 ('quote_mint', 'pubkey'),
 ('lp_mint', 'pubkey'),
 ('pool_base_token_account', 'pubkey'),
 ('pool_quote_token_account', 'pubkey'),
 ('lp_supply', 'u64'),
 ('coin_creator', 'pubkey'),
 ('is_mayhem_mode', 'bool'),
 ('is_cashback_coin', 'bool'),
 ('virtual_quote_reserves', 'i128'),
 ('creator_fee_bps', 'u64'),
 ('can_edit_creator_fee', 'bool'),
 ('is_holder_reward', 'bool'),
 ('protocol_fees', 'u64'),
 ('creator_fees', 'u64')]


def key(raw):
    alphabet='123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';n=int.from_bytes(raw,'big');value=''
    while n:n,r=divmod(n,58);value=alphabet[r]+value
    return '1'*(len(raw)-len(raw.lstrip(b'\0')))+value


def fields(raw,schema,required):
    offset=8;values={}
    for index,(name,kind) in enumerate(schema):
        if offset==len(raw) and index>=required:break
        size={'u8':1,'bool':1,'u16':2,'u64':8,'i64':8,'i128':16,'pubkey':32}.get(kind)
        if kind=='string':
            if offset+4>len(raw):raise ValueError('PumpSwap string truncated')
            size=4+int.from_bytes(raw[offset:offset+4],'little')
            if size>68:raise ValueError('PumpSwap string oversized')
        if size is None or offset+size>len(raw):raise ValueError('PumpSwap event truncated')
        data=raw[offset:offset+size];offset+=size
        if kind=='pubkey':value=key(data)
        elif kind=='bool':
            if data[0] not in (0,1):raise ValueError('PumpSwap boolean invalid')
            value=bool(data[0])
        elif kind=='string':value=data[4:].decode('utf-8')
        else:value=int.from_bytes(data,'little',signed=kind.startswith('i'))
        values[name]=value
    return values,raw[offset:]


def decode_trade(raw):
    if raw[:8] not in (BUY_DISC,SELL_DISC):return None
    if len(raw)>2048:raise ValueError('PumpSwap event oversized')
    buy=raw[:8]==BUY_DISC;schema=BUY_FIELDS if buy else SELL_FIELDS
    required=next(i for i,f in enumerate(schema) if f[0]=='coin_creator_fee')+1
    values,tail=fields(raw,schema,required)
    if buy and values.get('ix_name','buy') not in ('buy','buy_exact_quote_in','buy_v2','buy_exact_quote_in_v2'):
        raise ValueError('PumpSwap buy instruction unknown')
    values.update(platform='PumpSwap',is_buy=buy,unparsed_tail_bytes=len(tail))
    if values['base_amount_out' if buy else 'base_amount_in']<=0:raise ValueError('PumpSwap zero trade')
    return values


def decode_pool(result,address):
    account=result.get('value') if isinstance(result,dict) else None
    if not isinstance(account,dict) or account.get('owner')!=PROGRAM or account.get('executable'):
        raise ValueError('PumpSwap pool owner invalid')
    data=account.get('data')
    if not isinstance(data,list) or len(data)!=2 or data[1]!='base64':raise ValueError('PumpSwap pool encoding invalid')
    raw=base64.b64decode(data[0],validate=True)
    if raw[:8]!=POOL_DISC or not 243<=len(raw)<=301:raise ValueError('PumpSwap pool layout unknown')
    required=next(i for i,f in enumerate(POOL_FIELDS) if f[0]=='coin_creator')+1
    pool,tail=fields(raw,POOL_FIELDS,required)
    # Current allocated account includes fourteen zero padding bytes.
    if tail and (len(raw)!=301 or len(tail)!=14 or any(tail)):raise ValueError('PumpSwap pool extension unknown')
    if pool['base_mint']==pool['quote_mint']:raise ValueError('PumpSwap identical pool mints')
    pool['address']=address;return pool


def normalize(event,pool):
    if event['pool']!=pool['address'] or event['coin_creator']!=pool['coin_creator']:
        raise ValueError('PumpSwap pool creator changed')
    buy=event['is_buy'];quote=pool['quote_mint']
    return dict(event,mint=pool['base_mint'],creator=event['coin_creator'],quote_mint=quote,
                quote_amount=event['quote_amount_in' if buy else 'quote_amount_out'],
                token_amount=event['base_amount_out' if buy else 'base_amount_in'],
                sol_amount=event['quote_amount_in' if buy else 'quote_amount_out'] if quote=='So11111111111111111111111111111111111111112' else 0)
