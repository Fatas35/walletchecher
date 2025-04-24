import os
import time
import sys
import random
import asyncio
import aiohttp
import json
from mnemonic import Mnemonic
from eth_account import Account
from web3 import Web3
from web3.exceptions import Web3ValidationError
import logging
from typing import Dict, List, Tuple, Any, Set, Optional
from concurrent.futures import ProcessPoolExecutor
import multiprocessing

# --- Constants ---
NON_EMPTY_FILENAME = "non_empty_wallets.txt"
EMPTY_FILENAME = "empty_wallets.txt"
DEFAULT_RPC_TIMEOUT = 3  # Reduced timeout for faster error detection
DEFAULT_WORKERS_SUGGESTION = min(multiprocessing.cpu_count() * 2, 64)
BATCH_SAVE_SIZE = 5000  # Increased batch size for less frequent I/O
MAX_CONCURRENT_RPC_REQUESTS = 200  # Limit concurrent API requests
REQUEST_HEADERS = {"Content-Type": "application/json"}  # Common headers
IO_WRITE_BUFFER_SIZE = 8192 * 16  # Larger buffer for file operations
ENTROPY_BATCH_SIZE = 1000  # Pre-generate entropy for wallet creation

# --- Configure Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger('wallet_generator')

# --- HD Wallet Feature Activation ---
Account.enable_unaudited_hdwallet_features()

# --- Network Configuration ---
NETWORKS = {
    "Ethereum": {
        "rpc_urls": [
            "https://eth.llamarpc.com",
            "https://eth-mainnet.public.blastapi.io"
        ],
        "symbol": "ETH",
        "method": "eth_getBalance",
        "priority": 1  # Check high-priority networks first
    },
    "BSC": {
        "rpc_urls": [
            "https://bsc-dataseed.binance.org/",
            "https://bsc-dataseed1.defibit.io/",
            "https://bsc-dataseed1.ninicoin.io/",
            "https://bsc.publicnode.com",
            "https://binance.llamarpc.com",
        ],
        "symbol": "BNB",
        "method": "eth_getBalance",
        "priority": 2
    },
    "Polygon": {
        "rpc_urls": [
            "https://polygon-rpc.com/",
            "https://polygon.publicnode.com"
        ],
        "symbol": "MATIC",
        "method": "eth_getBalance",
        "priority": 3
    },
    "Avalanche": {
        "rpc_urls": [
            "https://api.avax.network/ext/bc/C/rpc",
            "https://avax.public-rpc.com",
            "https://avalanche-c-chain.publicnode.com",
            "https://blastapi.io/public-api/avalanche",
        ],
        "symbol": "AVAX",
        "method": "eth_getBalance",
        "priority": 4
    },
    "Arbitrum": {
        "rpc_urls": [
            "https://arb1.arbitrum.io/rpc",
            "https://arbitrum.llamarpc.com",
            "https://arbitrum-one.public.blastapi.io",
            "https://arbitrum.publicnode.com",
        ],
        "symbol": "ETH",
        "method": "eth_getBalance",
        "priority": 3
    },
    "Optimism": {
        "rpc_urls": [
            "https://mainnet.optimism.io",
            "https://optimism.llamarpc.com",
            "https://optimism-mainnet.public.blastapi.io",
            "https://optimism.publicnode.com",
        ],
        "symbol": "ETH",
        "method": "eth_getBalance",
        "priority": 5
    }
}

# --- Global Variables ---
ACTIVE_NETWORKS: Dict[str, Dict[str, Any]] = {}
output_lock = multiprocessing.Lock()  # Lock for thread-safe file writing
entropy_pool = []  # Pre-generated randomness for wallet creation
RPC_SUCCESS_RATES: Dict[str, Dict[str, float]] = {}  # Track success rates of RPCs
io_buffer = {"non_empty": [], "empty": []}  # Buffer for file writes

# --- Helper Functions ---
async def test_rpc_connection(session, network_name, url):
    """Tests an RPC endpoint with HTTP request."""
    try:
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_blockNumber",
            "params": [],
            "id": 1
        }
        
        async with session.post(url, json=payload, headers=REQUEST_HEADERS, timeout=DEFAULT_RPC_TIMEOUT) as response:
            if response.status != 200:
                return None
                
            response_json = await response.json()
            if 'result' not in response_json:
                return None
                
            # Convert hex to int
            block_num = int(response_json['result'], 16)
            return {
                "url": url,
                "block": block_num,
                "latency": response.elapsed.total_seconds() if hasattr(response, 'elapsed') else 0
            }
    except Exception as e:
        return None

async def initialize_rpc_connections():
    """Initializes and tests all RPC connections asynchronously."""
    logger.info("Initializing and testing RPC connections...")
    
    global ACTIVE_NETWORKS
    
    async with aiohttp.ClientSession() as session:
        tasks = []
        for network, config in NETWORKS.items():
            if not config.get('rpc_urls'):
                logger.warning(f"No RPC URLs configured for {network}.")
                continue
                
            for url in config['rpc_urls']:
                tasks.append(test_rpc_connection(session, network, url))
        
        results = await asyncio.gather(*tasks)
        
        # Process results and select best RPCs
        network_results = {}
        for network, config in NETWORKS.items():
            network_results[network] = []
            
        idx = 0
        for network, config in NETWORKS.items():
            if not config.get('rpc_urls'):
                continue
                
            for url in config['rpc_urls']:
                if idx < len(results) and results[idx] is not None:
                    network_results[network].append({
                        "url": url,
                        "block": results[idx]["block"],
                        "success_rate": 1.0,  # Initial success rate
                        "latency": results[idx].get("latency", 0)
                    })
                idx += 1
        
        # Initialize active networks with working RPCs
        for network, rpcs in network_results.items():
            if rpcs:
                # Sort by latency for initial selection
                rpcs.sort(key=lambda x: x.get("latency", float('inf')))
                
                # Create entry in active networks
                ACTIVE_NETWORKS[network] = {
                    "rpcs": rpcs,
                    "symbol": NETWORKS[network]["symbol"],
                    "method": NETWORKS[network]["method"],
                    "priority": NETWORKS[network]["priority"]
                }
                logger.info(f"✅ {network}: {len(rpcs)} working RPCs")
            else:
                logger.warning(f"⚠ No working RPCs for {network}")
    
    # Log summary
    if not ACTIVE_NETWORKS:
        logger.error("Fatal Error: No usable RPC connections established across any network.")
        sys.exit(1)
        
    networks_by_priority = sorted(ACTIVE_NETWORKS.keys(), 
                                   key=lambda net: ACTIVE_NETWORKS[net]["priority"])
    logger.info(f"Using {len(ACTIVE_NETWORKS)} active networks with "
               f"{sum(len(v['rpcs']) for v in ACTIVE_NETWORKS.values())} total RPC connections.")
    logger.info(f"Priority order: {', '.join(networks_by_priority)}")

def pregenerate_entropy(count):
    """Pre-generates random entropy for wallet creation to speed up the process."""
    global entropy_pool
    mnemo = Mnemonic("english")
    entropy_pool = [os.urandom(16) for _ in range(count)]  # 16 bytes = 128 bits
    return len(entropy_pool)

def generate_wallet_from_entropy(entropy_data):
    """Generates wallet from pre-computed entropy."""
    mnemo = Mnemonic("english")
    phrase = mnemo.to_mnemonic(entropy_data)
    account = Account.from_mnemonic(phrase)
    return {
        "phrase": phrase,
        "address": account.address,
        "private_key": account.key.hex()  # Optional, can be removed if not needed
    }

def batch_generate_wallets(batch_size, start_index=0):
    """Generates multiple wallets efficiently."""
    global entropy_pool
    
    # Generate more entropy if needed
    if len(entropy_pool) < batch_size:
        pregenerate_entropy(max(batch_size, ENTROPY_BATCH_SIZE))
    
    wallets = []
    for i in range(batch_size):
        if entropy_pool:
            entropy = entropy_pool.pop()
            wallet = generate_wallet_from_entropy(entropy)
            wallet["index"] = start_index + i
            wallets.append(wallet)
    
    return wallets

async def check_balance_async(session, network, rpc_url, address):
    """Checks balance for a specific address using JSON-RPC."""
    try:
        # Convert to checksum address
        if not address.startswith('0x'):
            address = '0x' + address
        
        # Ensure address is properly formatted (web3 would do this, but we're using direct RPC)
        address = Web3.to_checksum_address(address)
        
        method = ACTIVE_NETWORKS[network]["method"]
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": [address, "latest"],
            "id": 1
        }
        
        async with session.post(rpc_url, json=payload, headers=REQUEST_HEADERS, timeout=DEFAULT_RPC_TIMEOUT) as response:
            if response.status != 200:
                return network, f"HTTP Error: {response.status}", 0
                
            response_json = await response.json()
            
            if 'error' in response_json:
                return network, f"RPC Error: {response_json['error'].get('message', 'Unknown error')}", 0
                
            if 'result' not in response_json:
                return network, "No result in response", 0
                
            balance_hex = response_json['result']
            balance_wei = int(balance_hex, 16)
            
            if balance_wei > 0:
                balance_native = balance_wei / 10**18  # Convert wei to main unit
                return network, f"{balance_native:.18f} {ACTIVE_NETWORKS[network]['symbol']}".rstrip('0').rstrip('.'), balance_native
            else:
                return network, f"0 {ACTIVE_NETWORKS[network]['symbol']}", 0
                
    except asyncio.TimeoutError:
        return network, f"Timeout on {network}", 0
    except Exception as e:
        return network, f"Error: {type(e).__name__}", 0

async def check_balances_for_addresses(addresses, session):
    """Checks balances for multiple addresses across networks efficiently."""
    results = []
    batched_tasks = []
    
    # Get networks ordered by priority
    prioritized_networks = sorted(ACTIVE_NETWORKS.keys(), 
                                 key=lambda net: ACTIVE_NETWORKS[net].get("priority", 999))
    
    # Create a flat list of all balance check tasks
    for address_data in addresses:
        address = address_data["address"]
        address_results = {"address": address, "balances": {}, "has_balance": False}
        
        for network in prioritized_networks:
            # Select RPC based on success rate and latency
            rpcs = ACTIVE_NETWORKS[network]["rpcs"]
            if not rpcs:
                continue
                
            # Pick RPC with highest success rate, with some randomization to distribute load
            rpc_data = random.choices(
                rpcs, 
                weights=[r.get("success_rate", 0.5) for r in rpcs],
                k=1
            )[0]
            
            url = rpc_data["url"]
            
            task = check_balance_async(session, network, url, address)
            batched_tasks.append((address, task))
    
    # Execute all tasks concurrently but with rate limiting
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_RPC_REQUESTS)
    
    async def bounded_fetch(address, task):
        async with semaphore:
            return address, await task
    
    bounded_tasks = [bounded_fetch(addr, task) for addr, task in batched_tasks]
    task_results = await asyncio.gather(*bounded_tasks, return_exceptions=True)
    
    # Process results
    for address_data in addresses:
        address = address_data["address"]
        address_results = {
            "index": address_data["index"],
            "phrase": address_data["phrase"],
            "address": address,
            "balances": {},
            "has_balance": False,
            "error": None
        }
        
        # Filter results for this address
        address_balance_results = [r[1] for r in task_results if r[0] == address and not isinstance(r, Exception)]
        
        for result in address_balance_results:
            if isinstance(result, Exception):
                continue
                
            network, balance_str, balance_value = result
            address_results["balances"][network] = balance_str
            
            # Check for non-zero balance
            if balance_value > 0:
                address_results["has_balance"] = True
                # Early exit for high-priority networks if balance found
                # This optimization avoids checking low-priority networks unnecessarily
                if ACTIVE_NETWORKS[network]["priority"] <= 2:  # Only for high priority networks
                    break
        
        results.append(address_results)
    
    return results

def buffer_wallet_data(wallet_data):
    """Adds wallet data to the buffer for later batch saving."""
    global io_buffer
    
    lines = []
    lines.append(f"Index: {wallet_data['index']}")
    lines.append(f"Phrase: {wallet_data['phrase']}")
    lines.append(f"Address: {wallet_data['address']}")
    
    has_balance = wallet_data['has_balance'] and not wallet_data.get('error')
    
    if has_balance:
        for network, balance in sorted(wallet_data.get('balances', {}).items()):
            if network in ACTIVE_NETWORKS:
                lines.append(f" {network}: {balance}")
        lines.append("-" * 20)
        lines.append("")
        io_buffer["non_empty"].append("\n".join(lines))
    else:
        if wallet_data.get('error'):
            lines.append(f" Error: {wallet_data['error']}")
        lines.append("-" * 20)
        lines.append("")
        io_buffer["empty"].append("\n".join(lines))
    
    return has_balance

def flush_buffer(force=False):
    """Flushes the buffer to disk if it's large enough or forced."""
    global io_buffer
    
    with output_lock:
        if io_buffer["non_empty"] and (force or len(io_buffer["non_empty"]) >= 100):
            try:
                with open(NON_EMPTY_FILENAME, "a", encoding="utf-8", buffering=IO_WRITE_BUFFER_SIZE) as f:
                    f.write("\n".join(io_buffer["non_empty"]))
                count_non_empty = len(io_buffer["non_empty"])
                io_buffer["non_empty"] = []
            except Exception as e:
                logger.error(f"Error saving non-empty wallets: {e}")
                count_non_empty = 0
        else:
            count_non_empty = 0
            
        if io_buffer["empty"] and (force or len(io_buffer["empty"]) >= 500):
            try:
                with open(EMPTY_FILENAME, "a", encoding="utf-8", buffering=IO_WRITE_BUFFER_SIZE) as f:
                    f.write("\n".join(io_buffer["empty"]))
                count_empty = len(io_buffer["empty"])
                io_buffer["empty"] = []
            except Exception as e:
                logger.error(f"Error saving empty wallets: {e}")
                count_empty = 0
        else:
            count_empty = 0
    
    return count_non_empty, count_empty

async def process_batch(batch_start, batch_size):
    """Processes a batch of wallets: generates and checks balances."""
    # Generate wallets
    wallets = batch_generate_wallets(batch_size, batch_start)
    
    # Check balances asynchronously
    async with aiohttp.ClientSession() as session:
        results = await check_balances_for_addresses(wallets, session)
    
    # Buffer results
    non_empty_count = 0
    for result in results:
        if buffer_wallet_data(result):
            non_empty_count += 1
    
    # Potentially flush buffer if large enough
    saved_non_empty, saved_empty = flush_buffer(False)
    
    return {
        "processed": len(results),
        "has_balance": non_empty_count,
        "empty": len(results) - non_empty_count,
        "saved_non_empty": saved_non_empty,
        "saved_empty": saved_empty
    }

async def main_async():
    """Main async function that orchestrates the wallet generation and checking process."""
    script_start_time = time.time()
    
    # Initialize RPC connections
    await initialize_rpc_connections()
    
    # Clear or create output files at the start
    for filename in [NON_EMPTY_FILENAME, EMPTY_FILENAME]:
        try:
            with open(filename, "w", encoding="utf-8") as f:
                pass
            logger.info(f"Cleared/Created file: {filename}")
        except OSError as e:
            logger.warning(f"Warning: Could not clear file {filename}: {e}")
    
    # Get user input for configuration
    while True:
        try:
            logger.info("\n--- Configuration ---")
            logger.info(f"Suggesting up to {DEFAULT_WORKERS_SUGGESTION} workers based on CPU cores.")
            max_workers_input = input(f"Enter number of parallel workers (processes) [Default: {DEFAULT_WORKERS_SUGGESTION}]: ")
            max_workers = int(max_workers_input) if max_workers_input else DEFAULT_WORKERS_SUGGESTION
            
            num_phrases_input = input("How many wallets (mnemonics) to generate? (e.g., 10000): ")
            num_phrases_to_generate = int(num_phrases_input)
            
            batch_size_input = input(f"Process wallets in batches of how many? [Default: {BATCH_SAVE_SIZE}]: ")
            batch_size = int(batch_size_input) if batch_size_input else BATCH_SAVE_SIZE
            
            if num_phrases_to_generate >= 1 and max_workers >= 1 and batch_size >= 1:
                break
            print("Please enter valid numbers (>= 1 for all inputs).")
        except ValueError:
            print("Invalid input. Please enter whole numbers.")
    
    # Pre-generate entropy for wallets
    logger.info(f"Pre-generating entropy for faster wallet creation...")
    pregenerate_entropy(min(num_phrases_to_generate, ENTROPY_BATCH_SIZE))
    
    logger.info(f"\n🚀 Starting generation of {num_phrases_to_generate} wallets with {max_workers} workers...")
    logger.info(f"\tChecking across {len(ACTIVE_NETWORKS)} active networks")
    logger.info(f"\tProcessing in batches of {batch_size} wallets")
    
    # Initialize counters
    non_empty_count = 0
    empty_count = 0
    processed_count = 0
    error_count = 0
    
    generation_start_time = time.time()
    
    # Calculate number of batches
    num_batches = (num_phrases_to_generate + batch_size - 1) // batch_size
    batches = [(i * batch_size, min(batch_size, num_phrases_to_generate - i * batch_size)) 
               for i in range(num_batches)]
    
    # Process batches with semaphore to limit concurrency
    sem = asyncio.Semaphore(max_workers)
    
    async def bounded_process(batch_start, batch_size):
        async with sem:
            return await process_batch(batch_start, batch_size)
    
    tasks = [bounded_process(start, size) for start, size in batches]
    
    # Process batches and update progress
    batch_results = []
    for i, task in enumerate(asyncio.as_completed(tasks)):
        try:
            result = await task
            processed_count += result["processed"]
            non_empty_count += result["has_balance"]
            empty_count += result["processed"] - result["has_balance"]
            
            # Update progress
            progress = (processed_count / num_phrases_to_generate) * 100
            logger.info(f"\rBatch {i+1}/{num_batches} complete | "
                        f"Progress: {progress:.1f}% | "
                        f"Non-Empty: {non_empty_count} | "
                        f"Empty: {empty_count}")
            
        except Exception as e:
            error_count += 1
            logger.error(f"Error processing batch: {e}")
    
    # Final flush of buffers
    final_non_empty, final_empty = flush_buffer(True)
    logger.info(f"Final flush: Saved {final_non_empty} non-empty and {final_empty} empty wallets")
    
    generation_end_time = time.time()
    generation_duration = generation_end_time - generation_start_time
    
    # Final Summary
    total_duration = time.time() - script_start_time
    wallets_per_second = (processed_count / generation_duration) if generation_duration > 0 else 0
    
    logger.info("\n" + "="*40 + "\n\t📊 Final Summary 📊\n" + "="*40)
    logger.info(f"Total Wallets Requested : {num_phrases_to_generate}")
    logger.info(f"Total Wallets Processed : {processed_count}")
    logger.info(f" Non-Empty Wallets Found: {non_empty_count}")
    logger.info(f" Empty/Error Wallets\t : {empty_count} (includes {error_count} processing errors)")
    logger.info(f"Total Wallets Saved\t : {processed_count}")
    logger.info("-" * 40)
    logger.info(f"Active Networks Checked : {len(ACTIVE_NETWORKS)}")
    logger.info(f"Successful RPC Endpoints : {sum(len(v['rpcs']) for v in ACTIVE_NETWORKS.values())}")
    logger.info(f"Workers Used\t : {max_workers}")
    logger.info(f"Batch Size\t\t : {batch_size}")
    logger.info("-" * 40)
    logger.info(f"Total Generation Time\t : {generation_duration:.2f} seconds")
    logger.info(f"Processing Speed\t : {wallets_per_second:.2f} wallets/second")
    logger.info(f"Total Execution Time\t : {total_duration:.2f} seconds")
    logger.info("-" * 40)
    logger.info(f"Non-empty wallets saved to: {NON_EMPTY_FILENAME}")
    logger.info(f"Empty/Error wallets saved to: {EMPTY_FILENAME}")
    logger.info("="*40)

def main():
    """Entry point that sets up asyncio event loop."""
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    try:
        import mnemonic
        import eth_account
        import web3
        import aiohttp
    except ImportError as e:
        print(f"Error: Missing dependency - {e}")
        print("Please install required libraries:")
        print("pip install mnemonic eth_account web3 aiohttp")
        sys.exit(1)
    
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\nScript interrupted by user. Saving remaining data...")
        try:
            # Attempt to flush remaining data
            non_empty, empty = flush_buffer(True)
            print(f"Saved {non_empty} non-empty and {empty} empty wallets before exit.")
        except Exception as e:
            print(f"Error during final save: {e}")
        sys.exit(0)

if __name__ == "__main__":
    multiprocessing.freeze_support()  # For Windows compatibility if packaged
    main()