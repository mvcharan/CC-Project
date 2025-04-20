from flask import Flask, request, jsonify, render_template
import docker
import uuid
import threading
import time

app = Flask(__name__, template_folder='templates')
client = docker.from_env()

nodes = {}  # {id: {container_id, cpu_cores, status, pods: []}}
pods = {}   # {id: {node_id, cpu_request}}

def heartbeat_monitor():
    while True:
        for node_id in list(nodes.keys()):
            try:
                container = client.containers.get(nodes[node_id]['container_id'])
                container.reload()
                nodes[node_id]['status'] = container.status
            except Exception:
                nodes[node_id]['status'] = "unreachable"

                # Failure Recovery 
                print(f"[Recovery] Node {node_id} is unreachable. Attempting to migrate its pods...")

                for pod_id in nodes[node_id]['pods'][:]:  
                    pod = pods[pod_id]
                    new_node = schedule_pod(pod['cpu_request'])
                    if new_node:
                        pods[pod_id]['node_id'] = new_node
                        nodes[new_node]['pods'].append(pod_id)
                        nodes[node_id]['pods'].remove(pod_id)
                        print(f"[Recovery] Pod {pod_id} migrated to Node {new_node}")
                    else:
                        print(f"[Recovery] No available node to migrate pod {pod_id}")
        time.sleep(10)

threading.Thread(target=heartbeat_monitor, daemon=True).start()

# Home route renders webpage
@app.route('/')
def index():
    return render_template('index.html', nodes=nodes, pods=pods)

@app.route('/add_node', methods=['POST'])
def add_node():
    node_id = str(uuid.uuid4())
    cpu_cores = int(request.form.get('cpu_cores', 1))
    try:
        container = client.containers.run("alpine", command="sleep infinity", detach=True, name=f"node_{node_id}")
        nodes[node_id] = {
            'container_id': container.id,
            'cpu_cores': cpu_cores,
            'status': 'running',
            'pods': []
        }
        return jsonify({"message": "Node added", "node_id": node_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/list_nodes')
def list_nodes():
    return jsonify(nodes)


@app.route('/stop_node/<node_id>', methods=['DELETE'])
@app.route('/stop_node/<node_id>', methods=['DELETE'])
def stop_node(node_id):
    if node_id not in nodes:
        return jsonify({"error": "Node not found"}), 404
    try:
        container = client.containers.get(nodes[node_id]['container_id'])
        container.stop()
        container.remove()
        orphaned_pods = [pod_id for pod_id in nodes[node_id]['pods']]
        migrated_pods = []
        failed_pods = []

        for pod_id in orphaned_pods:
            pod = pods[pod_id]
            new_node = schedule_pod(pod['cpu_request'])
            if new_node:
                pods[pod_id]['node_id'] = new_node
                nodes[new_node]['pods'].append(pod_id)
                migrated_pods.append(pod_id)
            else:
                failed_pods.append(pod_id)

        del nodes[node_id]

        return jsonify({
            "message": "Node and its pods removed",
            "node_id": node_id,
            "removed_pods": orphaned_pods,
            "migrated_pods": migrated_pods,
            "failed_pods": failed_pods
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/launch_pod', methods=['POST'])
def launch_pod():
    pod_id = str(uuid.uuid4())
    cpu_request = int(request.form.get('cpu_request', 1))
    assigned_node = schedule_pod(cpu_request)
    if not assigned_node:
        return jsonify({"error": "No available node"}), 400
    pods[pod_id] = {'node_id': assigned_node, 'cpu_request': cpu_request}
    nodes[assigned_node]['pods'].append(pod_id)
    return jsonify({"message": "Pod launched", "pod_id": pod_id, "assigned_node": assigned_node})

def schedule_pod(cpu_request):
    for node_id, node in nodes.items():
        if node['status'] == 'running':
            used = sum(pods[p]['cpu_request'] for p in node['pods'])
            if used + cpu_request <= node['cpu_cores']:
                return node_id
    return None

@app.route('/pod_status/<pod_id>')
def pod_status(pod_id):
    if pod_id not in pods:
        return jsonify({"error": "Pod not found"}), 404
    return jsonify({"pod_id": pod_id, "node_id": pods[pod_id]['node_id']})

if __name__ == '__main__':
   app.run(debug=True, host='127.0.0.1', port=5005)
