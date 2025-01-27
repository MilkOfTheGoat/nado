import asyncio
import ipaddress
import json
import os
import signal
import socket
import sys

import msgpack
import tornado.ioloop
import tornado.web

import config
import versioner
from block_ops import get_block, get_latest_block_info, fee_over_blocks
from config import get_config
from loops.consensus_loop import ConsensusClient
from loops.core_loop import CoreClient
from data_ops import set_and_sort, get_home
from genesis import make_genesis, make_folders
from keys import keyfile_found, generate_keys, save_keys, load_keys
from log_ops import get_logger
from memserver import MemServer
from loops.message_loop import MessageClient
from loops.peer_loop import PeerClient
from peer_ops import save_peer, get_remote_status, get_producer_set
from transaction_ops import get_transaction, get_transactions_of_account
from account_ops import get_account


def is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("localhost", port)) == 0


def handler(signum, frame):
    logger.info("Terminating..")
    memserver.terminate = True
    #tornado.ioloop.IOLoop.current().stop()
    sys.exit(0)


def serialize(output, name=None, compress=None):
    if compress == "msgpack":
        output = msgpack.packb(output)
    elif not isinstance(output, dict) and name:
        output = {name: output}
    return output


class HomeHandler(tornado.web.RequestHandler):
    def home(self):
        self.render("templates/homepage.html", ip=get_config()["ip"])

    def get(self):
        self.home()


class StatusHandler(tornado.web.RequestHandler):
    def status(self):
        compress = StatusHandler.get_argument(self, "compress", default="none")

        try:
            status_dict = {
                "reported_uptime": memserver.reported_uptime,
                "address": memserver.address,
                "transaction_pool_hash": memserver.transaction_pool_hash,
                "block_producers_hash": memserver.block_producers_hash,
                "latest_block_hash": get_latest_block_info(logger=logger)["block_hash"],
                "protocol": memserver.protocol,
            }

            self.write(serialize(name="status",
                                 output=status_dict,
                                 compress=compress))

        except Exception as e:
            self.set_status(403)
            self.write(f"Error: {e}")

    async def get(self, parameter):
        await asyncio.to_thread(self.status)


class TransactionPoolHandler(tornado.web.RequestHandler):
    def transaction_pool(self):
        compress = TransactionPoolHandler.get_argument(self, "compress", default="none")
        transaction_pool_data = memserver.transaction_pool
        self.write(serialize(name="transaction_pool",
                             output=transaction_pool_data,
                             compress=compress))

    async def get(self, parameter):
        await asyncio.to_thread(self.transaction_pool)


class TransactionBufferHandler(tornado.web.RequestHandler):
    def transaction_buffer(self):
        compress = TransactionBufferHandler.get_argument(self, "compress", default="none")
        buffer_data = memserver.tx_buffer

        self.write(serialize(name="transaction_buffer",
                             output=buffer_data,
                             compress=compress))

    async def get(self, parameter):
        await asyncio.to_thread(self.transaction_buffer)


class TrustPoolHandler(tornado.web.RequestHandler):
    def trust_pool(self):
        compress = TrustPoolHandler.get_argument(self, "compress", default="none")
        trust_pool_data = consensus.trust_pool

        self.write(serialize(name="trust_pool_data",
                             output=trust_pool_data,
                             compress=compress,
                             ))

    async def get(self, parameter):
        await asyncio.to_thread(self.trust_pool)


class PeerPoolHandler(tornado.web.RequestHandler):
    def peer_pool(self):
        compress = PeerPoolHandler.get_argument(self, "compress", default="none")
        peers_data = list(memserver.peers)

        self.write(serialize(name="peers",
                             output=peers_data,
                             compress=compress
                             ))

    async def get(self, parameter):
        await asyncio.to_thread(self.peer_pool)


class BlockProducerPoolHandler(tornado.web.RequestHandler):
    def block_producers(self):
        compress = BlockProducerPoolHandler.get_argument(self, "compress", default="none")
        producer_data = list(memserver.block_producers)

        self.write(serialize(name="block_producers",
                             output=producer_data,
                             compress=compress))

    async def get(self, parameter):
        await asyncio.to_thread(self.block_producers)


class BlockProducersHashPoolHandler(tornado.web.RequestHandler):
    def block_producers_hash_pool(self):
        compress = BlockProducersHashPoolHandler.get_argument(self, "compress", default="none")

        output = {
            "block_producers_hash_pool": consensus.block_producers_hash_pool,
            "majority_block_producers_hash_pool": consensus.majority_block_producers_hash,
        }

        self.write(serialize(name="block_producers_hash_pool",
                             output=output,
                             compress=compress))

    async def get(self, parameter):
        await asyncio.to_thread(self.block_producers_hash_pool)


class TransactionHashPoolHandler(tornado.web.RequestHandler):
    def transaction_hash_pool(self):
        compress = TransactionHashPoolHandler.get_argument(self, "compress", default="none")

        output = {
            "transactions_hash_pool": consensus.transaction_hash_pool,
            "majority_transactions_hash_pool": consensus.majority_transaction_pool_hash,
        }

        self.write(serialize(name="transactions_hash_pool",
                             output=output,
                             compress=compress))

    async def get(self, parameter):
        await asyncio.to_thread(self.transaction_hash_pool)


class BlockHashPoolHandler(tornado.web.RequestHandler):
    def block_hash_pool(self):
        compress = BlockHashPoolHandler.get_argument(self, "compress", default="none")

        output = {
            "block_opinions": consensus.block_hash_pool,
            "majority_block_opinion": consensus.majority_block_hash,
        }

        self.write(serialize(name="block_hash_pool",
                             output=output,
                             compress=compress))

    async def get(self, parameter):
        await asyncio.to_thread(self.block_hash_pool)


class FeeHandler(tornado.web.RequestHandler):
    def fee(self):
        self.write({"fee": fee_over_blocks(logger=logger)})

    async def get(self):
        await asyncio.to_thread(self.fee)


class StatusPoolHandler(tornado.web.RequestHandler):
    def status_pool(self):
        compress = StatusPoolHandler.get_argument(self, "compress", default="none")
        status_pool_data = consensus.status_pool

        self.write(serialize(name="status_pool",
                             output=status_pool_data,
                             compress=compress))

    async def get(self, parameter):
        await asyncio.to_thread(self.status_pool)


class SubmitTransactionHandler(tornado.web.RequestHandler):
    def submit_transaction(self):
        try:
            transaction_raw = SubmitTransactionHandler.get_argument(self, "data")
            transaction = json.loads(transaction_raw)

            output = memserver.merge_transaction(transaction, user_origin=True)
            self.write(msgpack.packb(output))

            if not output["result"]:
                self.set_status(403)

        except Exception as e:
            self.write(msgpack.packb(f"Invalid tx structure: {e}"))
            raise

    async def get(self, parameter):
        await asyncio.to_thread(self.submit_transaction)


class LogHandler(tornado.web.RequestHandler):
    def log(self):
        compress = LogHandler.get_argument(self, "compress", default="none")

        with open(f"{get_home()}/logs/log.log") as logfile:
            lines = logfile.readlines()
            for line in lines:
                if compress == "msgpack":
                    output = msgpack.packb(line)
                else:
                    output = line
                self.write(output)
                self.write("<br>")

    async def get(self, parameter):
        await asyncio.to_thread(self.log)


class TerminateHandler(tornado.web.RequestHandler):
    def terminate(self):
        try:
            server_key = TerminateHandler.get_argument(self, "key")

            if server_key == memserver.server_key:
                memserver.terminate = True
                #tornado.ioloop.IOLoop.current().stop()
        except Exception as e:
            self.set_status(403)
            self.write(f"Error: {e}")

    async def get(self, parameter):
        await asyncio.to_thread(self.terminate)


class TransactionHandler(tornado.web.RequestHandler):
    def transaction(self):
        try:
            transaction = TransactionHandler.get_argument(self, "txid")
            transaction_data = get_transaction(transaction, logger=logger)
            compress = TransactionHandler.get_argument(self, "compress", default="none")

            if not transaction_data:
                transaction_data = "Not found"
                self.set_status(403)

            self.write(serialize(name="transaction",
                                 output=transaction_data,
                                 compress=compress))

        except Exception as e:
            self.set_status(403)
            self.write(f"Error: {e}")

    async def get(self, parameter):
        await asyncio.to_thread(self.transaction)


class AccountTransactionsHandler(tornado.web.RequestHandler):
    """get transactions from a transaction index batch"""
    """batch takes number or max"""

    def account_transactions(self):
        try:
            address = AccountTransactionsHandler.get_argument(self, "address")
            batch = AccountTransactionsHandler.get_argument(self, "batch")
            compress = AccountTransactionsHandler.get_argument(self, "compress", default="none")

            transaction_data = get_transactions_of_account(account=address,
                                                           logger=logger,
                                                           batch=batch)

            if not transaction_data:
                transaction_data = "Not found"
                self.set_status(403)

            self.write(serialize(name="account_transactions",
                                 output=transaction_data,
                                 compress=compress))
        except Exception as e:
            self.set_status(403)
            self.write(f"Error: {e}")

    async def get(self, parameter):
        await asyncio.to_thread(self.account_transactions)


class GetBlockHandler(tornado.web.RequestHandler):
    def block(self):
        try:
            block = GetBlockHandler.get_argument(self, "hash")
            compress = GetBlockHandler.get_argument(self, "compress", default="none")
            block_data = get_block(block)

            if not block_data:
                block_data = "Not found"
                self.set_status(403)

            self.write(serialize(name="block",
                                 output=block_data,
                                 compress=compress))

        except Exception as e:
            self.set_status(403)
            self.write(f"Error: {e}")

    async def get(self, parameter):
        await asyncio.to_thread(self.block)


class GetBlocksBeforeHandler(tornado.web.RequestHandler):

    def blocks_before(self):
        try:
            block_hash = GetBlocksBeforeHandler.get_argument(self, "hash")
            count = int(GetBlocksBeforeHandler.get_argument(self, "count"))
            compress = GetBlocksBeforeHandler.get_argument(self, "compress", default="none")

            parent_hash = get_block(block_hash)["parent_hash"]

            collected_blocks = []
            for blocks in range(0, count):
                try:

                    block = get_block(parent_hash)
                    next_block = None
                    if next_block == block:
                        break

                    elif block:
                        collected_blocks.append(block)
                        parent_hash = block["parent_hash"]
                except Exception as e:
                    logger.debug("Block collection hit a roadblock")
                    break

            collected_blocks.reverse()

            if not collected_blocks:
                collected_blocks = "Not found"
                self.set_status(403)

            self.write(serialize(name="blocks_before",
                                 output=collected_blocks,
                                 compress=compress
                                 ))


        except Exception as e:
            self.set_status(403)
            self.write(f"Error: {e}")

    async def get(self, parameter):
        await asyncio.to_thread(self.blocks_before)


class GetBlocksAfterHandler(tornado.web.RequestHandler):
    def blocks_after(self):
        try:
            block_hash = GetBlocksAfterHandler.get_argument(self, "hash")
            count = int(GetBlocksAfterHandler.get_argument(self, "count"))
            compress = GetBlocksAfterHandler.get_argument(self, "compress", default="none")

            child_hash = get_block(block_hash)["child_hash"]

            collected_blocks = []

            for blocks in range(0, count):
                try:

                    previous_block = None
                    block = get_block(child_hash)
                    if previous_block == block:
                        break

                    elif block:
                        collected_blocks.append(block)
                        child_hash = block["child_hash"]

                except Exception as e:
                    logger.debug("Block collection hit a roadblock")
                    break

            if not collected_blocks:
                collected_blocks = "Not found"
                self.set_status(403)

            self.write(serialize(name="blocks_after",
                                 output=collected_blocks,
                                 compress=compress,
                                 ))

        except Exception as e:
            self.set_status(403)
            self.write(f"Error: {e}")

    async def get(self, parameter):
        await asyncio.to_thread(self.blocks_after)


class GetLatestBlockHandler(tornado.web.RequestHandler):
    def latest_block(self):
        latest_block_data = get_latest_block_info(logger=logger)
        compress = GetLatestBlockHandler.get_argument(self, "compress", default="none")

        self.write(serialize(name="latest_block",
                             output=latest_block_data,
                             compress=compress))

    async def get(self, parameter):
        await asyncio.to_thread(self.latest_block)


class AccountHandler(tornado.web.RequestHandler):
    def account(self):
        try:
            account = AccountHandler.get_argument(self, "address")
            compress = AccountHandler.get_argument(self, "compress", default="none")
            account_data = get_account(account, create_on_error=False)

            if not account_data:
                account_data = "Not found"
                self.set_status(403)

            self.write(serialize(name="account",
                                 output=account_data,
                                 compress=compress))

        except Exception as e:
            self.set_status(403)
            self.write(f"Error: {e}")

    async def get(self, parameter):
        await asyncio.to_thread(self.account)


class ProducerSetHandler(tornado.web.RequestHandler):
    def producer_set(self):
        try:
            producer_set_hash = ProducerSetHandler.get_argument(self, "hash")
            compress = ProducerSetHandler.get_argument(self, "compress", default="none")

            producer_data = get_producer_set(producer_set_hash)

            if not producer_data:
                producer_data = "Not found"
                self.set_status(403)

            self.write(serialize(name="producer_set",
                                 output=producer_data,
                                 compress=compress))
        except Exception as e:
            self.set_status(403)
            self.write(f"Error: {e}")

    async def get(self, parameter):
        await asyncio.to_thread(self.producer_set)


class AnnouncePeerHandler(tornado.web.RequestHandler):
    def announce(self):
        try:
            peer_ip = AnnouncePeerHandler.get_argument(self, "ip")
            assert ipaddress.ip_address(peer_ip)

            if peer_ip == "127.0.0.1" or peer_ip == get_config()["ip"]:
                self.write("Cannot add home address")
            else:

                if peer_ip in memserver.block_producers and peer_ip in memserver.unreachable.keys():
                    memserver.unreachable.pop(peer_ip)
                    logger.info(f"Restored {peer_ip} because of majority vote on its block production presence")

                if peer_ip not in memserver.peers and peer_ip not in memserver.unreachable.keys():
                    status = asyncio.run(get_remote_status(peer_ip, logger=logger))

                    assert status, f"{peer_ip} unreachable"

                    address = status["address"]
                    protocol = status["protocol"]

                    assert address, "No address detected"
                    assert protocol >= config.get_config()["protocol"], f"Protocol of {peer_ip} is too low"

                    save_peer(ip=peer_ip,
                              address=address,
                              port=get_config()["port"],
                              overwrite=True
                              )

                    if peer_ip not in memserver.peers + memserver.peer_buffer:
                        if memserver.period == 3:
                            memserver.peer_buffer.append(peer_ip)
                            memserver.peer_buffer = set_and_sort(memserver.peer_buffer)
                        elif len(memserver.peers) < memserver.peer_limit:
                            memserver.peers.append(peer_ip)
                            memserver.peers = set_and_sort(memserver.peers)

                    message = f"Peer {peer_ip} added"
                else:
                    message = f"Peer {peer_ip} is known or invalid"
                self.write(message)

        except Exception as e:
            self.set_status(403)
            self.write(f"Error: {e}")

    async def get(self, parameter):
        await asyncio.to_thread(self.announce)


async def make_app(port):
    application = tornado.web.Application(
        [
            (r"/", HomeHandler),
            (r"/get_transactions_of_account(.*)", AccountTransactionsHandler),
            (r"/get_transaction(.*)", TransactionHandler),
            (r"/get_blocks_after(.*)", GetBlocksAfterHandler),
            (r"/get_blocks_before(.*)", GetBlocksBeforeHandler),
            (r"/get_block(.*)", GetBlockHandler),
            (r"/get_account(.*)", AccountHandler),
            (r"/get_producer_set_from_hash(.*)", ProducerSetHandler),
            (r"/transaction_pool(.*)", TransactionPoolHandler),
            (r"/transaction_hash_pool(.*)", TransactionHashPoolHandler),
            (r"/transaction_buffer(.*)", TransactionBufferHandler),
            (r"/trust_pool(.*)", TrustPoolHandler),
            (r"/get_latest_block(.*)", GetLatestBlockHandler),
            (r"/announce_peer(.*)", AnnouncePeerHandler),
            (r"/status_pool(.*)", StatusPoolHandler),
            (r"/status(.*)", StatusHandler),
            (r"/peers(.*)", PeerPoolHandler),
            (r"/block_producers_hash_pool(.*)", BlockProducersHashPoolHandler),
            (r"/block_producers(.*)", BlockProducerPoolHandler),
            (r"/block_hash_pool(.*)", BlockHashPoolHandler),
            (r"/get_recommended_fee", FeeHandler),
            (r"/terminate(.*)", TerminateHandler),
            (r"/submit_transaction(.*)", SubmitTransactionHandler),
            (r"/log(.*)", LogHandler),
            (r"/static/(.*)", tornado.web.StaticFileHandler, {"path": "static"}),
            (r'/(favicon.ico)', tornado.web.StaticFileHandler, {"path": "graphics"}),

        ]
    )

    application.listen(port)
    await asyncio.Event().wait()


if __name__ == "__main__":
    """warning, no intensive operations or locks should be invoked from API interface"""
    if sys.platform == "win32" and sys.version_info >= (3, 8, 0):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    logger = get_logger()
    logger.info(f"NADO version {versioner.get_version()} started")

    if not os.path.exists(f"{get_home()}/blocks"):
        make_folders()
        make_genesis(
            address="ndo18c3afa286439e7ebcb284710dbd4ae42bdaf21b80137b",
            balance=1000000000000000000,
            ip="78.102.98.72",
            port=9173,
            timestamp=1669852800,
            logger=logger,
        )

    if not keyfile_found():
        save_keys(generate_keys())
        save_peer(ip=get_config()["ip"],
                  address=load_keys()["address"],
                  port=get_config()["port"],
                  peer_trust=10000)

    assert not is_port_in_use(get_config()["port"]), "Port already in use, exiting"
    signal.signal(signal.SIGINT, handler)

    memserver = MemServer(logger=logger)

    consensus = ConsensusClient(memserver=memserver, logger=logger)
    consensus.start()

    core = CoreClient(memserver=memserver, consensus=consensus, logger=logger)
    core.start()

    peers = PeerClient(memserver=memserver, consensus=consensus, logger=logger)
    peers.start()

    messages = MessageClient(memserver=memserver, consensus=consensus, core=core, peers=peers, logger=logger)
    messages.start()

    logger.info("Starting Request Handler")

    asyncio.run(make_app(get_config()["port"]))