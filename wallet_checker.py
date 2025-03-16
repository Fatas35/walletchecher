import os
from mnemonic import Mnemonic
from eth_account import Account
from web3 import Web3
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

# Enable HDWallet features
Account.enable_unaudited_hdwallet_features()

# Dictionary of blockchains with RPC URLs
NETWORKS = {
    "Ethereum": {"rpc_url": f"https://mainnet.infura.io/v3/{os.getenv('INFURA_API_KEY')}", "symbol": "ETH"},
    "BSC": {"rpc_url": f"https://bsc-dataseed.binance.org/", "symbol": "BNB"},
    "Polygon": {"rpc_url": "https://polygon-rpc.com/", "symbol": "MATIC"},
    "Avalanche": {"rpc_url": "https://api.avax.network/ext/bc/C/rpc", "symbol": "AVAX"},
    "Arbitrum": {"rpc_url": "https://arb1.arbitrum.io/rpc", "symbol": "ETH"},
    "Optimism": {"rpc_url": "https://mainnet.optimism.io", "symbol": "ETH"}
}

# Initialize Web3 connections with verification
def init_web3_instances():
    instances = {}
    for network, config in NETWORKS.items():
        web3 = Web3(Web3.HTTPProvider(config["rpc_url"]))
        if web3.is_connected():
            instances[network] = web3
        else:
            print(f"âš ï¸ {network} : Connexion RPC Ã©chouÃ©e")
    return instances

WEB3_INSTANCES = init_web3_instances()

# Check a specific blockchain
def check_balance(network, address):
    if network not in WEB3_INSTANCES:
        return network, "âš ï¸ RPC connection failed", 0

    web3 = WEB3_INSTANCES[network]
    try:
        balance = web3.eth.get_balance(address)
        balance_in_eth = web3.from_wei(balance, 'ether')
        return network, f"{balance_in_eth} {NETWORKS[network]['symbol']}", float(balance_in_eth)
    except Exception as e:
        return network, f"Erreur : {str(e)}", 0

# Check balances in parallel
def check_balances(address):
    total_balance = 0
    results = {}

    with ThreadPoolExecutor(max_workers=5) as executor:  # Limiter le nombre de threads actifs
        futures = {executor.submit(check_balance, network, address): network for network in WEB3_INSTANCES}

        for future in futures:
            network, result, balance_value = future.result()
            results[network] = result
            total_balance += balance_value

    return results, total_balance > 0

# Generate and check a wallet
def generate_and_check_wallet(wallet_number):
    mnemo = Mnemonic("english")
    phrase = mnemo.generate(strength=128)
    account = Account.from_mnemonic(phrase)
    address = account.address
    
    balances, has_balance = check_balances(address)

    print(f"\nðŸ“œ Mnemonic Phrase {wallet_number} : {phrase}")
    print(f"ðŸ¦ Address {wallet_number} : {address}")
    for network, balance in balances.items():
        print(f"ðŸ”— {network} : {balance}")

    return phrase, address, balances, has_balance

# Save wallets
def save_wallets(non_empty_wallets, empty_wallets):
    if non_empty_wallets:
        with open("non_empty_wallets.txt", "a") as f:
            for phrase, address, balances in non_empty_wallets:
                f.write(f"Phrase: {phrase}\nAdresse: {address}\n")
                for network, balance in balances.items():
                    f.write(f"{network}: {balance}\n")
                f.write("\n")
        print(f"{len(non_empty_wallets)} non-empty wallets added to non_empty_wallets.txt")

    if empty_wallets:
        with open("empty_wallets.txt", "a") as f:
            for phrase, address in empty_wallets:
                f.write(f"Phrase: {phrase}\nAdresse: {address}\n\n")
        print(f"{len(empty_wallets)} empty wallets added to empty_wallets.txt")

# Wallet generation and verification process
def process_wallets(num_phrases, max_workers=4):
    non_empty_wallets = []
    empty_wallets = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(generate_and_check_wallet, i) for i in range(1, num_phrases + 1)]

        for future in futures:
            phrase, address, balances, has_balance = future.result()
            if has_balance:
                non_empty_wallets.append((phrase, address, balances))
            else:
                empty_wallets.append((phrase, address))

    save_wallets(non_empty_wallets, empty_wallets)

def main():
    if not WEB3_INSTANCES:
        print("Error: No valid RPC connection. Check your RPC URLs.")
        return

    while True:
        try:
            num_phrases = int(input("How many mnemonic phrases do you want to generate? (1 or more): "))
            if num_phrases >= 1:
                break
            print("Please enter a number greater than or equal to 1.")
        except ValueError:
            print("Please enter a valid number.")

    process_wallets(num_phrases)

if __name__ == "__main__":
    try:
        import mnemonic, eth_account, web3
    except ImportError as e:
        print(f"Error: A dependency is missing - {e}")
        exit(1)

    main()
