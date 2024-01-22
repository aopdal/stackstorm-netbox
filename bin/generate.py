"""
Generate actions from NetBox API schema from OpenAPI v3 specification.
"""

import argparse
import os

import jinja2
import requests

RUNNING_DIR_NAME = os.path.dirname(__file__)
ACTIONS_DIR = os.path.join(RUNNING_DIR_NAME, '../actions')


def sanitize_parameters(parameters):
    for parameter in parameters:
        if parameter['name'] == 'tags':
            parameter['description'] = 'Array of tag strings'

        if parameter.get('schema'):
            if parameter['schema']['type'] == 'number':
                parameter['type'] = 'integer'
        else:
            if parameter['type'] == 'number':
                parameter['type'] = 'integer'

    return parameters


def parse_component_properties(properties, required):
    """
    Parse component properties from OpenAPI v3 specification.
    """
    parameters = []
    for name, data in properties.items():
        if data.get('readOnly'):
            continue

        parameter = {'name': name, 'type': data.get('type', 'object'),
                     'description': data.get('title', data.get('description', name.replace('_', ' ').capitalize()))}

        if name in required:
            parameter['required'] = True
        else:
            parameter['required'] = False

        parameters.append(parameter)
    return sanitize_parameters(parameters)


def get_actions(spec):
    """
    Generate actions from NetBox API schema from OpenAPI v3 specification.
    """
    print('Generating actions...')
    actions = {}
    deferred_detail_gets = []
    for path, path_spec in spec['paths'].items():
        path = path.replace('/api', '')
        path_parts = [x.replace('-', '_') for x in path.replace('/{id}', '').strip('/').split('/')]

        for method, method_spec in path_spec.items():
            if method == 'parameters':
                continue

            action_name = f'{method}.{".".join(path_parts)}'
            if '{id}' in path:
                path = path.replace('{id}', '{{ id }}')

            action = {
                'description': method_spec['description'],
                'parameters': [],
                'endpoint_uri': path,
                'immutable': True,
                'verb': method,
                'get_detail_route_eligible': True,
            }

            print(f'Processing {action_name} ...')
            ref = method_spec.get('requestBody', {}).get('content', {}).get('application/json', {}).get('schema',
                                                                                                        {}).get('$ref')

            if ref:
                ref_name = ref.split('/')[-1]
                schema = spec['components']['schemas'][ref_name]
                try:
                    required = ['id'] if method == 'patch' else schema['required']
                except KeyError:
                    required = []
                action['parameters'] = parse_component_properties(schema['properties'], required)

            if method == 'get':
                if method_spec['operationId'].endswith('_list'):
                    action['parameters'] = sanitize_parameters(method_spec['parameters'])
                    actions[action_name] = action

                elif path.endswith('/{{ id }}'):
                    # defer these until we have processed everything else to ensure the list
                    # endpoints are present for lookup
                    deferred_detail_gets.append(action_name)
                elif '{{ id }}' in path and not path.endswith('/{{ id }}'):
                    action['parameters'].append({
                        'name': 'id',
                        'required': True,
                        'description': f'ID of the object.',
                        'type': 'integer',
                    })
                    action['get_detail_route_eligible'] = False
                    actions[action_name] = action

            if method in ['delete', 'put', 'patch']:
                action['parameters'].append({
                    'name': 'id',
                    'required': True,
                    'description': f'ID of the object to {method}.',
                    'type': 'integer',
                })
                actions[action_name] = action

            if method == 'post':
                if '{{ id }}' not in path:
                    actions[action_name] = action

    # process deferred detail get endpoints
    for detailed_get in deferred_detail_gets:
        list_action = actions.get(detailed_get)
        if list_action is None:
            raise Exception("Unable to find list action for deferred GET endpoint {}".format(detailed_get))

    return actions


def delete_actions():
    """
    Removes all actions from the actions directory.
    """
    print(f'Deleting all actions from {ACTIONS_DIR}')
    current_actions_listing = os.listdir(ACTIONS_DIR)
    for item in current_actions_listing:
        if item.endswith('.yaml'):
            os.remove(os.path.join(ACTIONS_DIR, item))


def write_actions(version, actions):
    # Render new actions and write them to file
    with open('action-template.jinja2', 'r') as f:
        template = jinja2.Template(f.read(), autoescape=True)

    for name, action in actions.items():
        template_vars = {
            'version': version,
            'action_name': name,
            **action
        }
        rendered = template.render(template_vars)
        f = open(os.path.join(ACTIONS_DIR, f'{name}.yaml'), 'w')
        f.write(rendered)
        f.close()

    print(f'Wrote {len(actions)} actions to {ACTIONS_DIR}')


def main():
    """
    Main entry point.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=str, default='https://demo.netbox.dev', help='NetBox hostname')
    parser.add_argument('--no-verify-ssl', action='store_false', help='Disable SSL certificate verification')

    args = parser.parse_args()
    host = str(args.host).rstrip('/')

    try:
        print(f'Connecting to {host}...')
        response = requests.get(f'{host}/api/schema?format=json', verify=args.no_verify_ssl)
        response.raise_for_status()
        spec = response.json()
    except requests.RequestException as e:
        print(f'Failed to fetch schema: {e}')
        exit(1)

    # Generate actions from schema
    actions = get_actions(spec)
    # Delete all existing actions
    delete_actions()
    # Write actions to file
    version = spec['info']['version'].split(' ')[0]  # parses '3.6.9 (3.6)' to '3.6.9'
    write_actions(version, actions)


if __name__ == '__main__':
    main()
