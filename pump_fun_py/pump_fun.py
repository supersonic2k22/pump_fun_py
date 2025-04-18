import struct
import argparse
from solana.rpc.commitment import Processed
from solana.rpc.types import TokenAccountOpts, TxOpts
from spl.token.instructions import create_associated_token_account, get_associated_token_address
from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
from solders.instruction import Instruction, AccountMeta
from solders.message import MessageV0
from solders.transaction import VersionedTransaction
from solana.rpc.api import Client
from solders.keypair import Keypair
from dotenv import load_dotenv
import os
from constants import *
from utils import confirm_txn, get_token_balance
from coin_data import get_coin_data, sol_for_tokens, tokens_for_sol

# Завантажуємо .env
load_dotenv()

# Конфігурація
PRIV_KEY = os.getenv("PRIV_KEY")
RPC = os.getenv("RPC")
UNIT_BUDGET = os.getenv("UNIT_BUDGET")
UNIT_PRICE = os.getenv("UNIT_PRICE")

# Перевірка наявності змінних
if not all([PRIV_KEY, RPC, UNIT_BUDGET, UNIT_PRICE]):
    raise ValueError("Missing required environment variables in .env")

# Ініціалізація
client = Client(RPC)
payer_keypair = Keypair.from_base58_string(PRIV_KEY)
UNIT_BUDGET = int(UNIT_BUDGET)
UNIT_PRICE = int(UNIT_PRICE)

def buy(mint_str: str, sol_in: float = 0.01, slippage: int = 5) -> bool:
    print(f"Calling buy with mint: {mint_str}, sol_in: {sol_in}, slippage: {slippage}")
    try:
        print(f"Starting buy transaction for mint: {mint_str}")
        coin_data = get_coin_data(mint_str)
        print(f"Coin data: {coin_data}")
        
        if not coin_data:
            print("Failed to retrieve coin data.")
            return False

        if coin_data.complete:
            print("Warning: This token has bonded and is only tradable on PumpSwap.")
            return False

        MINT = coin_data.mint
        BONDING_CURVE = coin_data.bonding_curve
        ASSOCIATED_BONDING_CURVE = coin_data.associated_bonding_curve
        USER = payer_keypair.pubkey()

        print("Fetching or creating associated token account...")
        
        token_account_check = client.get_token_accounts_by_owner(payer_keypair.pubkey(), TokenAccountOpts(MINT), Processed)
        
        if token_account_check.value:
            ASSOCIATED_USER = token_account_check.value[0].pubkey
            token_account_instruction = None
            print("Existing token account found.")
        else:
            ASSOCIATED_USER = get_associated_token_address(USER, MINT)
            token_account_instruction = create_associated_token_account(USER, USER, MINT)
            print(f"Creating token account : {ASSOCIATED_USER}")

        print("Calculating transaction amounts...")
        sol_dec = 1e9
        token_dec = 1e6
        virtual_sol_reserves = coin_data.virtual_sol_reserves / sol_dec
        virtual_token_reserves = coin_data.virtual_token_reserves / token_dec
        amount = sol_for_tokens(sol_in, virtual_sol_reserves, virtual_token_reserves)
        amount = int(amount * token_dec)
        
        slippage_adjustment = 1 + (slippage / 100)
        max_sol_cost = int((sol_in * slippage_adjustment) * sol_dec)
        print(f"Amount: {amount} | Max Sol Cost: {max_sol_cost}")

        print("Creating swap instructions...")
        keys = [
            AccountMeta(pubkey=GLOBAL, is_signer=False, is_writable=False),
            AccountMeta(pubkey=FEE_RECIPIENT, is_signer=False, is_writable=True),
            AccountMeta(pubkey=MINT, is_signer=False, is_writable=False),
            AccountMeta(pubkey=BONDING_CURVE, is_signer=False, is_writable=True),
            AccountMeta(pubkey=ASSOCIATED_BONDING_CURVE, is_signer=False, is_writable=True),
            AccountMeta(pubkey=ASSOCIATED_USER, is_signer=False, is_writable=True),
            AccountMeta(pubkey=USER, is_signer=True, is_writable=True),
            AccountMeta(pubkey=SYSTEM_PROGRAM, is_signer=False, is_writable=False),
            AccountMeta(pubkey=TOKEN_PROGRAM, is_signer=False, is_writable=False),
            AccountMeta(pubkey=RENT, is_signer=False, is_writable=False),
            AccountMeta(pubkey=EVENT_AUTHORITY, is_signer=False, is_writable=False),
            AccountMeta(pubkey=PUMP_FUN_PROGRAM, is_signer=False, is_writable=False)
        ]

        data = bytearray()
        data.extend(bytes.fromhex("66063d1201daebea"))
        data.extend(struct.pack('<Q', amount))
        data.extend(struct.pack('<Q', max_sol_cost))
        swap_instruction = Instruction(PUMP_FUN_PROGRAM, bytes(data), keys)

        instructions = [
            set_compute_unit_limit(UNIT_BUDGET),
            set_compute_unit_price(UNIT_PRICE),
        ]
        
        if token_account_instruction:
            instructions.append(token_account_instruction)
        instructions.append(swap_instruction)

        print("Compiling transaction message...")
        compiled_message = MessageV0.try_compile(
            payer_keypair.pubkey(),
            instructions,
            [],
            client.get_latest_blockhash().value.blockhash,
        )

        print("Sending transaction...")
        txn_sig = client.send_transaction(
            txn=VersionedTransaction(compiled_message, [payer_keypair]),
            opts=TxOpts(skip_preflight=True)
        ).value
        print(f"Transaction Signature: {txn_sig}")

        print("Confirming transaction...")
        confirmed = confirm_txn(txn_sig)
        
        print(f"Transaction confirmed: {confirmed}")
        return confirmed

    except Exception as e:
        print(f"Error occurred during transaction: {e}")
        import traceback
        traceback.print_exc()
        return False

def sell(mint_str: str, percentage: int = 100, slippage: int = 5) -> bool:
    print(f"Calling sell with mint: {mint_str}, percentage: {percentage}, slippage: {slippage}")
    try:
        print(f"Starting sell transaction for mint: {mint_str}")

        if not (1 <= percentage <= 100):
            print("Percentage must be between 1 and 100.")
            return False

        coin_data = get_coin_data(mint_str)
        
        if not coin_data:
            print("Failed to retrieve coin data.")
            return False

        if coin_data.complete:
            print("Warning: This token has bonded and is only tradable on PumpSwap.")
            return False

        MINT = coin_data.mint
        BONDING_CURVE = coin_data.bonding_curve
        ASSOCIATED_BONDING_CURVE = coin_data.associated_bonding_curve
        USER = payer_keypair.pubkey()
        ASSOCIATED_USER = get_associated_token_address(USER, MINT)

        print("Retrieving token balance...")
        token_balance = get_token_balance(payer_keypair.pubkey(), MINT)
        if token_balance == 0 or token_balance is None:
            print("Token balance is zero. Nothing to sell.")
            return False
        print(f"Token Balance: {token_balance}")
        
        print("Calculating transaction amounts...")
        sol_dec = 1e9
        token_dec = 1e6
        token_balance = token_balance * (percentage / 100)
        amount = int(token_balance * token_dec)
        
        virtual_sol_reserves = coin_data.virtual_sol_reserves / sol_dec
        virtual_token_reserves = coin_data.virtual_token_reserves / token_dec
        sol_out = tokens_for_sol(token_balance, virtual_sol_reserves, virtual_token_reserves)
        
        slippage_adjustment = 1 - (slippage / 100)
        min_sol_output = int((sol_out * slippage_adjustment) * sol_dec)
        print(f"Amount: {amount} | Minimum Sol Out: {min_sol_output}")

        print("Creating swap instructions...")
        keys = [
            AccountMeta(pubkey=GLOBAL, is_signer=False, is_writable=False),
            AccountMeta(pubkey=FEE_RECIPIENT, is_signer=False, is_writable=True),
            AccountMeta(pubkey=MINT, is_signer=False, is_writable=False),
            AccountMeta(pubkey=BONDING_CURVE, is_signer=False, is_writable=True),
            AccountMeta(pubkey=ASSOCIATED_BONDING_CURVE, is_signer=False, is_writable=True),
            AccountMeta(pubkey=ASSOCIATED_USER, is_signer=False, is_writable=True),
            AccountMeta(pubkey=USER, is_signer=True, is_writable=True),
            AccountMeta(pubkey=SYSTEM_PROGRAM, is_signer=False, is_writable=False),
            AccountMeta(pubkey=ASSOC_TOKEN_ACC_PROG, is_signer=False, is_writable=False),
            AccountMeta(pubkey=TOKEN_PROGRAM, is_signer=False, is_writable=False),
            AccountMeta(pubkey=EVENT_AUTHORITY, is_signer=False, is_writable=False),
            AccountMeta(pubkey=PUMP_FUN_PROGRAM, is_signer=False, is_writable=False)
        ]

        data = bytearray()
        data.extend(bytes.fromhex("33e685a4017f83ad"))
        data.extend(struct.pack('<Q', amount))
        data.extend(struct.pack('<Q', min_sol_output))
        swap_instruction = Instruction(PUMP_FUN_PROGRAM, bytes(data), keys)

        instructions = [
            set_compute_unit_limit(UNIT_BUDGET),
            set_compute_unit_price(UNIT_PRICE),
            swap_instruction,
        ]

        print("Compiling transaction message...")
        compiled_message = MessageV0.try_compile(
            payer_keypair.pubkey(),
            instructions,
            [],
            client.get_latest_blockhash().value.blockhash,
        )

        print("Sending transaction...")
        txn_sig = client.send_transaction(
            txn=VersionedTransaction(compiled_message, [payer_keypair]),
            opts=TxOpts(skip_preflight=True)
        ).value
        print(f"Transaction Signature: {txn_sig}")

        print("Confirming transaction...")
        confirmed = confirm_txn(txn_sig)
        
        print(f"Transaction confirmed: {confirmed}")
        return confirmed

    except Exception as e:
        print(f"Error occurred during transaction: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    # Налаштування аргументів командного рядка
    parser = argparse.ArgumentParser(description="Run buy or sell function for PumpFun")
    parser.add_argument("--action", choices=["buy", "sell"], required=True, help="Action to perform: buy or sell")
    parser.add_argument("--mint", type=str, required=True, help="Mint address of the token")
    parser.add_argument("--sol", type=float, default=0.1, help="Amount of SOL to spend (for buy)")
    parser.add_argument("--percentage", type=int, default=100, help="Percentage of tokens to sell (for sell)")
    parser.add_argument("--slippage", type=int, default=5, help="Slippage percentage")

    args = parser.parse_args()

    # Перевірка балансу гаманця
    balance = client.get_balance(payer_keypair.pubkey()).value
    print(f"Wallet balance: {balance / 1e9} SOL")

    # Виклик відповідної функції залежно від дії
    if args.action == "buy":
        result = buy(args.mint, args.sol, args.slippage)
        print(f"Buy transaction result: {result}")
    elif args.action == "sell":
        result = sell(args.mint, args.percentage, args.slippage)
        print(f"Sell transaction result: {result}")