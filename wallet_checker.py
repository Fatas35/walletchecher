import os
from mnemonic import Mnemonic
from eth_account import Account
from web3 import Web3
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

# Charger les variables d'environnement depuis .env
load_dotenv()

# Activation des fonctionnalités HDWallet
Account.enable_unaudited_hdwallet_features()

# Dictionnaire des blockchains avec URLs RPC
NETWORKS = {
    "Ethereum": {"rpc_url": f"https://mainnet.infura.io/v3/{os.getenv('INFURA_API_KEY')}", "symbol": "ETH"},
    "BSC": {"rpc_url": f"https://bsc-dataseed.binance.org/", "symbol": "BNB"},
    "Polygon": {"rpc_url": "https://polygon-rpc.com/", "symbol": "MATIC"},
    "Avalanche": {"rpc_url": "https://api.avax.network/ext/bc/C/rpc", "symbol": "AVAX"},
    "Arbitrum": {"rpc_url": "https://arb1.arbitrum.io/rpc", "symbol": "ETH"},
    "Optimism": {"rpc_url": "https://mainnet.optimism.io", "symbol": "ETH"}
}

# Initialisation des connexions Web3 avec vérification
def init_web3_instances():
    instances = {}
    for network, config in NETWORKS.items():
        web3 = Web3(Web3.HTTPProvider(config["rpc_url"]))
        if web3.is_connected():
            instances[network] = web3
        else:
            print(f"⚠️ {network} : Connexion RPC échouée")
    return instances

WEB3_INSTANCES = init_web3_instances()

# Vérification d'une blockchain spécifique
def check_balance(network, address):
    if network not in WEB3_INSTANCES:
        return network, "⚠️ Connexion RPC échouée", 0

    web3 = WEB3_INSTANCES[network]
    try:
        balance = web3.eth.get_balance(address)
        balance_in_eth = web3.from_wei(balance, 'ether')
        return network, f"{balance_in_eth} {NETWORKS[network]['symbol']}", float(balance_in_eth)
    except Exception as e:
        return network, f"Erreur : {str(e)}", 0

# Vérification des soldes en parallèle
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

# Génération et vérification d'un wallet
def generate_and_check_wallet(wallet_number):
    mnemo = Mnemonic("english")
    phrase = mnemo.generate(strength=128)
    account = Account.from_mnemonic(phrase)
    address = account.address
    
    balances, has_balance = check_balances(address)

    print(f"\n📜 Phrase {wallet_number} : {phrase}")
    print(f"🏦 Adresse {wallet_number} : {address}")
    for network, balance in balances.items():
        print(f"🔗 {network} : {balance}")

    return phrase, address, balances, has_balance

# Sauvegarde des portefeuilles
def save_wallets(non_empty_wallets, empty_wallets):
    if non_empty_wallets:
        with open("non_empty_wallets.txt", "a") as f:
            for phrase, address, balances in non_empty_wallets:
                f.write(f"Phrase: {phrase}\nAdresse: {address}\n")
                for network, balance in balances.items():
                    f.write(f"{network}: {balance}\n")
                f.write("\n")
        print(f"{len(non_empty_wallets)} portefeuilles non vides ajoutés à non_empty_wallets.txt")

    if empty_wallets:
        with open("empty_wallets.txt", "a") as f:
            for phrase, address in empty_wallets:
                f.write(f"Phrase: {phrase}\nAdresse: {address}\n\n")
        print(f"{len(empty_wallets)} portefeuilles vides ajoutés à empty_wallets.txt")

# Processus de génération et vérification de wallets
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
        print("Erreur : Aucune connexion RPC valide. Vérifiez vos URLs RPC.")
        return

    while True:
        try:
            num_phrases = int(input("Combien de phrases mnémoniques voulez-vous générer ? (1 ou plus) : "))
            if num_phrases >= 1:
                break
            print("Veuillez entrer un nombre supérieur ou égal à 1.")
        except ValueError:
            print("Veuillez entrer un nombre valide.")

    process_wallets(num_phrases)

if __name__ == "__main__":
    try:
        import mnemonic, eth_account, web3
    except ImportError as e:
        print(f"Erreur : Une dépendance est manquante - {e}")
        exit(1)

    main()
